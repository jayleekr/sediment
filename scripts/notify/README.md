# hp-notify

> Cross-product notification dispatcher. Self-contained Python (httpx + jinja2 + pyyaml). Vendored from `hypeproof-harness` into every consumer repo via `sync.sh`. One module, one CLI, many products.

## Why

Sediment, hypeproof-studio, hypeprooflab-page all need to send Discord/Slack/email notifications on the same kinds of events (deploy success, recall regression, daily digest, new decision, …). Writing this three times = three drift-prone forks. Shipping it via the existing harness vendoring model = one canonical source, three rsync targets, zero coupling.

## How

```
notify.py send <event_type> --routes config/notify_routes.yaml \
                              --data sha=abc1234 --data branch=main \
                              [--tenant <slug>] [--dry-run]
```

What happens:
1. Load `routes.yaml`, validate schema (`version: 1`, channels dict, routes dict)
2. Look up the route for `(tenant or "*", event_type)`
3. Render `templates/<event_type>.md.j2` with the payload + tenant + severity
4. For each channel in the route: load `transports/<name>.py`, resolve target via `secret_env`/`url`/`to`, call `await transport.send(target, body)`
5. Print per-channel result; exit 0 (all sent), 2 (partial), 3 (all failed), 1 (config error)

Python API:
```python
from notify import notify
result = await notify(
    event_type="deploy.success",
    routes_path="config/notify_routes.yaml",
    payload={"sha": "abc1234", "branch": "main", "url": "..."},
    tenant_slug="hypeproof-lab",
)
```

## What it doesn't do (v1)

- Circuit breaker — placeholder file; logic comes in v1.1 once we have failure data
- Cooldown / dedup — same; needs `notification_log` table per design `07-notifications.md`
- Per-tenant template overrides from DB — v3 (UI-driven)

## Adding a new event type

1. Add `templates/<event_type_with_underscores>.md.j2` here
2. Add the event_type to each consumer's `routes.yaml` (or in `routes."*"` for default)
3. Add a caller that does `notify(event_type=..., payload=..., ...)`

That's it. No code change in `notify.py`.

## Adding a new transport

1. Add `transports/<name>.py` exposing `async def send(url, content) -> (status, err)`
2. Use it in `routes.yaml` as `transport: <name>`

## Routes schema (compact)

```yaml
version: 1

channels:
  sediment: { transport: discord_webhook, secret_env: DISCORD_WEBHOOK_SEDIMENT }
  email_jay: { transport: email_smtp, to: jay@hypeproof.io }

routes:
  "*":                                      # applies unless tenant override
    deploy.success: { channels: [sediment], template: deploy_success }
  hypeproof-lab:                            # per-tenant override
    new_decision:
      channels: [sediment, meeting-notes]
      template: new_decision
      severity: info
```

## Vendoring

Canonical source: `hypeproof-harness/scripts/notify/`
Vendored to: `<consumer>/scripts/notify/` (rsync via `sync.sh`)

Per-consumer files (NOT vendored):
- `config/notify_routes.yaml` — each product's own routing
- Secrets — fly secrets / GH org secrets / Vercel env per channel

Drift detection: `sync.sh --check` exits 1 if any consumer's vendored copy diverges from canonical.

## Local development

```bash
cd hypeproof-harness/scripts/notify
DISCORD_WEBHOOK_SEDIMENT="https://discord.com/api/webhooks/..." \
  python notify.py send deploy.success \
    --routes ../../../sediment/config/notify_routes.yaml \
    --data sha=abc1234 --data branch=main --data product=sediment
```

## Tested with

- Python 3.11+
- httpx 0.28+
- jinja2 3.1+
- pyyaml 6.0+

All of these are already in sediment's `pyproject.toml`. New consumers add them if not present.
