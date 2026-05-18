"""Stripe webhook + subscription management — Phase 7 stub.

Real implementation:
  - verify Stripe-Signature header against STRIPE_WEBHOOK_SECRET
  - on customer.subscription.created/updated → upsert subscriptions row
  - on invoice.payment_failed → tenant.status = 'suspended' (3-day grace)
  - on customer.subscription.deleted → tenant.status = 'cancelled'
"""
from __future__ import annotations
import json
from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import text

from lab_lib.db import service_session
from lab_lib.logging import get_logger

router = APIRouter()
log = get_logger("billing")


@router.post("/webhook")
async def stripe_webhook(request: Request, stripe_signature: str | None = Header(default=None)):
    """Stripe webhook endpoint. Phase 7 — stub now, real signature verify later."""
    body = await request.body()
    payload = json.loads(body or b"{}")
    event_type = payload.get("type", "")
    data = payload.get("data", {}).get("object", {})

    log.info("stripe.event", type=event_type, id=payload.get("id"))

    if event_type == "customer.subscription.updated":
        # Lookup tenant by stripe_customer_id, sync seat_count + plan
        await _sync_subscription(data)
    elif event_type == "invoice.payment_failed":
        await _suspend_tenant(data)
    elif event_type == "customer.subscription.deleted":
        await _cancel_tenant(data)

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
