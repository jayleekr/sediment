"""Stripe webhook + subscription management.

WO-1 2026-05-23 (REPORT.md CRIT #1) — signature verification added.

Previously this captured the `Stripe-Signature` header into a parameter and
ignored it. Anyone with the public webhook URL could POST a
`customer.subscription.deleted` event and flip arbitrary tenants to
`cancelled` / `suspended` / rewrite `seat_count`. Now:

  - Every event verified via stripe.Webhook.construct_event (signature +
    timestamp tolerance, default 5 min).
  - Dedup via stripe_event_log table (migration 003) — Stripe's 3-day retry
    window means we WILL see duplicates eventually.
  - Boot-time guard in lab_lib.settings.validate_runtime_secrets() refuses
    to start in prod if STRIPE_WEBHOOK_SECRET is empty.

If Stripe ever needs to be replaced, the only contract this module exports is:
  - POST /api/v1/billing/webhook accepts a Stripe-shaped event envelope
  - POST /api/v1/billing/checkout returns a checkout URL
Anything beyond the boundary stays in stripe-sdk land.
"""
from __future__ import annotations
import stripe
from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import text

from lab_lib.db import service_session
from lab_lib.logging import get_logger
from lab_lib.settings import settings

router = APIRouter()
log = get_logger("billing")


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None),
):
    """Stripe webhook endpoint with signature verification + dedup."""
    body = await request.body()

    if not stripe_signature:
        # Stripe always sends this header. Missing it = not from Stripe.
        log.warning("stripe.webhook.no_signature_header")
        raise HTTPException(status_code=400, detail="missing Stripe-Signature header")

    if not settings.stripe_webhook_secret:
        # Request-time defense in depth — boot guard already refuses to start
        # in prod with this empty, but in dev/staging we might be running
        # without the secret deliberately. Better to 503 than to silently
        # accept anything.
        log.error("stripe.webhook.secret_unconfigured")
        raise HTTPException(status_code=503, detail="webhook secret not configured")

    try:
        event = stripe.Webhook.construct_event(
            payload=body,
            sig_header=stripe_signature,
            secret=settings.stripe_webhook_secret,
        )
    except stripe.error.SignatureVerificationError:
        log.warning("stripe.webhook.signature_invalid")
        raise HTTPException(status_code=400, detail="invalid signature")
    except ValueError:
        # construct_event raises ValueError on malformed payload before
        # signature check. Same response shape — don't help attackers
        # distinguish "bad json" from "bad signature".
        log.warning("stripe.webhook.malformed_payload")
        raise HTTPException(status_code=400, detail="malformed payload")

    event_id = event["id"]
    event_type = event["type"]

    # Idempotency. Stripe retries failed deliveries for up to 3 days; a
    # network hiccup that drops our 200 = duplicate state flip without this.
    # See migration 003_stripe_event_log.sql for the table.
    async with service_session() as s:
        try:
            await s.execute(text("""
                INSERT INTO stripe_event_log (event_id, type)
                VALUES (:eid, :type)
            """), {"eid": event_id, "type": event_type})
            await s.commit()
        except Exception:
            # INSERT failed — almost certainly PK collision (already seen).
            # Could also be table missing in a dev env that didn't run
            # migration 003 yet — that's a clearly-loud server-side error,
            # not something we want to swallow as "deduped". Re-raise the
            # specific PK-violation path; everything else surfaces as 500.
            await s.rollback()
            # Re-check whether the event is in the log; if yes → dedup ack.
            r = await s.execute(text(
                "SELECT 1 FROM stripe_event_log WHERE event_id = :eid LIMIT 1"
            ), {"eid": event_id})
            if r.first():
                log.info("stripe.webhook.deduped", event_id=event_id, type=event_type)
                return {"received": True, "type": event_type, "deduped": True}
            # Real failure (table missing, etc.) — surface as 500 so Stripe
            # retries and we notice in our logs.
            raise

    log.info("stripe.webhook.accepted", event_id=event_id, type=event_type)

    data = event["data"]["object"]
    if event_type == "customer.subscription.updated":
        await _sync_subscription(data)
    elif event_type == "invoice.payment_failed":
        await _suspend_tenant(data)
    elif event_type == "customer.subscription.deleted":
        await _cancel_tenant(data)
    # Other event types are accepted (logged + ACKed) but unhandled — keeps
    # Stripe from retrying things we don't yet care about.

    return {"received": True, "type": event_type}


async def _sync_subscription(sub: dict):
    customer_id = sub.get("customer")
    if not customer_id:
        return
    seats = (sub.get("items", {}).get("data", [{}])[0].get("quantity")) or 1
    async with service_session() as s:
        await s.execute(text("""
            UPDATE subscriptions
            SET seat_count = :seats,
                stripe_subscription_id = :sid
            WHERE stripe_customer_id = :cid
        """), {"seats": seats, "sid": sub.get("id"), "cid": customer_id})


async def _suspend_tenant(invoice: dict):
    customer_id = invoice.get("customer")
    if not customer_id:
        return
    async with service_session() as s:
        await s.execute(text("""
            UPDATE tenants SET status = 'suspended'
            WHERE id IN (SELECT tenant_id FROM subscriptions WHERE stripe_customer_id = :cid)
        """), {"cid": customer_id})


async def _cancel_tenant(sub: dict):
    customer_id = sub.get("customer")
    if not customer_id:
        return
    async with service_session() as s:
        await s.execute(text("""
            UPDATE tenants SET status = 'cancelled'
            WHERE id IN (SELECT tenant_id FROM subscriptions WHERE stripe_customer_id = :cid)
        """), {"cid": customer_id})


@router.post("/checkout")
async def create_checkout_session():
    """Stub — real impl uses stripe.checkout.Session.create with seat-based price."""
    return {"checkout_url": "https://checkout.stripe.com/stub", "note": "Phase 7 — wire Stripe SDK"}
