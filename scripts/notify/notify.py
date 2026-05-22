#!/usr/bin/env python3
"""hypeproof-notify — cross-product notification dispatcher.

Canonical source in hypeproof-harness; vendored into each consumer repo
(sediment, hypeproof-studio, hypeprooflab, hypeprooflab-page) via sync.sh.
Same code, per-product routes.yaml + per-product secrets.

USAGE (CLI):
    notify.py send <event_type> --routes <path> [--data k=v ...] [--tenant <slug>]
    notify.py render <event_type> --template-dir <path> [--data k=v ...]
    notify.py validate-routes <path>

USAGE (Python):
    from notify import notify
    await notify(
        event_type="deploy.success",
        routes_path="config/notify_routes.yaml",
        payload={"sha": "abc1234", "branch": "main", "url": "..."},
        tenant_slug="hypeproof-lab",  # optional; uses '*' if absent
    )

DEPENDENCIES: httpx, jinja2, pyyaml. No db, no llm, no surprises.

CONTRACT (don't change without bumping HARNESS_VERSION):
- Single Python file. No package; no relative imports inside notify.py.
- Templates: jinja2 files in templates/, named <event_type>.md.j2 with dots
  replaced by underscores (e.g. deploy.success → deploy_success.md.j2).
- Transports: importable from transports/ as `<name>.py` exposing
  `async def send(url: str, content: str) -> tuple[int, str]`.
- routes.yaml schema: see routes_schema.py.
- Secrets resolved from env vars named in routes.yaml's `secret_env` field.

EXIT CODES (CLI):
    0  sent (or dry-run / render ok)
    1  config error (bad routes / bad template / missing secret)
    2  partial (some routes failed; others ok)
    3  all routes failed
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("FATAL: pyyaml not installed", file=sys.stderr); sys.exit(1)
try:
    from jinja2 import ChainableUndefined, Environment, FileSystemLoader, select_autoescape
except ImportError:
    print("FATAL: jinja2 not installed", file=sys.stderr); sys.exit(1)


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TEMPLATE_DIR = SCRIPT_DIR / "templates"
DEFAULT_TRANSPORTS_DIR = SCRIPT_DIR / "transports"
HARNESS_VERSION = "0.1.0"


@dataclass
class RouteResult:
    """One channel's send outcome — for logging + audit."""
    channel: str
    transport: str
    status: str                  # 'sent' | 'failed' | 'suppressed_cooldown' | 'suppressed_circuit'
    http_status: int | None = None
    error: str | None = None
    elapsed_ms: int = 0


@dataclass
class NotifyResult:
    """Whole-event outcome."""
    event_type: str
    tenant_slug: str | None
    routes_fired: list[RouteResult] = field(default_factory=list)
    rendered: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Routes loader + validator
# ---------------------------------------------------------------------------

def load_routes(path: str | Path) -> dict[str, Any]:
    """Read and validate the routes.yaml. Raises ValueError on bad shape."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"routes file not found: {p}")
    cfg = yaml.safe_load(p.read_text())
    _validate_routes(cfg)
    return cfg


def _validate_routes(cfg: dict) -> None:
    """Minimal pydantic-free schema check — keeps deps small."""
    if not isinstance(cfg, dict):
        raise ValueError("routes.yaml top-level must be a mapping")
    if cfg.get("version") != 1:
        raise ValueError("routes.yaml: missing or wrong `version: 1`")
    ch = cfg.get("channels") or {}
    if not isinstance(ch, dict):
        raise ValueError("routes.yaml: `channels` must be a mapping")
    for slug, spec in ch.items():
        if not isinstance(spec, dict) or "transport" not in spec:
            raise ValueError(f"routes.yaml: channel `{slug}` missing transport")
        if "secret_env" not in spec and "url" not in spec and "to" not in spec:
            raise ValueError(f"routes.yaml: channel `{slug}` needs secret_env / url / to")
    routes = cfg.get("routes") or {}
    if not isinstance(routes, dict):
        raise ValueError("routes.yaml: `routes` must be a mapping (tenant_slug → events)")


def _route_for(cfg: dict, tenant_slug: str | None, event_type: str) -> dict | None:
    """Return the rule for (tenant, event) — tenant override first, then '*'."""
    routes = cfg.get("routes") or {}
    if tenant_slug and tenant_slug in routes and event_type in routes[tenant_slug]:
        return routes[tenant_slug][event_type]
    if "*" in routes and event_type in routes["*"]:
        return routes["*"][event_type]
    return None


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------

def _make_env(template_dir: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape([]),     # markdown output, no html-escape
        # ChainableUndefined: missing vars render empty, support a.b.c lookup
        # without raising. Templates already guard with {% if x %} for empty
        # sections; this gives them the leeway to do so without StrictUndefined
        # blowing up on the bool() coercion.
        undefined=ChainableUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_event(
    event_type: str,
    payload: dict,
    template_name: str | None = None,
    template_dir: Path = DEFAULT_TEMPLATE_DIR,
    extra_context: dict | None = None,
) -> str:
    """Render the markdown body for one event."""
    env = _make_env(template_dir)
    tmpl_file = (template_name or event_type.replace(".", "_")) + ".md.j2"
    try:
        tmpl = env.get_template(tmpl_file)
    except Exception as e:
        raise FileNotFoundError(
            f"template `{tmpl_file}` not found in {template_dir}: {e}"
        )
    ctx = {**(extra_context or {}), **payload, "event_type": event_type}
    return tmpl.render(**ctx)


# ---------------------------------------------------------------------------
# Transport loader (dynamic import from transports/<name>.py)
# ---------------------------------------------------------------------------

_TRANSPORT_CACHE: dict[str, Any] = {}


def _load_transport(name: str, transports_dir: Path = DEFAULT_TRANSPORTS_DIR):
    if name in _TRANSPORT_CACHE:
        return _TRANSPORT_CACHE[name]
    path = transports_dir / f"{name}.py"
    if not path.is_file():
        raise FileNotFoundError(f"transport `{name}` not found at {path}")
    spec = importlib.util.spec_from_file_location(f"hp_transport_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "send"):
        raise AttributeError(f"transport `{name}` has no `send` function")
    _TRANSPORT_CACHE[name] = mod
    return mod


def _resolve_target(channel_spec: dict) -> tuple[str, str | None]:
    """Returns (target, error). Target is URL or email — depends on transport."""
    if "url" in channel_spec:
        return channel_spec["url"], None
    if "secret_env" in channel_spec:
        env = channel_spec["secret_env"]
        url = os.environ.get(env, "").strip()
        if not url:
            return "", f"env var `{env}` not set or empty"
        return url, None
    if "to" in channel_spec:
        return channel_spec["to"], None
    return "", "channel spec missing url / secret_env / to"


# ---------------------------------------------------------------------------
# Core entry — async notify()
# ---------------------------------------------------------------------------

async def notify(
    *,
    event_type: str,
    routes_path: str | Path,
    payload: dict | None = None,
    tenant_slug: str | None = None,
    template_dir: Path | None = None,
    transports_dir: Path | None = None,
    dry_run: bool = False,
) -> NotifyResult:
    """Send one event through every configured channel for (tenant, event)."""
    payload = payload or {}
    tdir = template_dir or DEFAULT_TEMPLATE_DIR
    xdir = transports_dir or DEFAULT_TRANSPORTS_DIR
    result = NotifyResult(event_type=event_type, tenant_slug=tenant_slug)

    try:
        cfg = load_routes(routes_path)
    except Exception as e:
        result.error = f"routes error: {e}"
        return result

    rule = _route_for(cfg, tenant_slug, event_type)
    if rule is None:
        result.error = f"no route for event `{event_type}` (tenant={tenant_slug or '*'})"
        return result

    channels_to_fire = rule.get("channels") or []
    template_name = rule.get("template")
    extra_context = {
        "tenant_slug": tenant_slug,
        "severity": rule.get("severity", "info"),
    }

    try:
        rendered = render_event(
            event_type, payload, template_name, tdir, extra_context,
        )
        result.rendered = rendered
    except Exception as e:
        result.error = f"render error: {e}"
        return result

    if dry_run:
        for ch in channels_to_fire:
            result.routes_fired.append(RouteResult(
                channel=ch, transport="(dry-run)", status="sent",
            ))
        return result

    channel_defs = cfg.get("channels") or {}
    for ch_slug in channels_to_fire:
        spec = channel_defs.get(ch_slug)
        if not spec:
            result.routes_fired.append(RouteResult(
                channel=ch_slug, transport="?",
                status="failed", error=f"channel `{ch_slug}` not in routes.channels",
            ))
            continue
        transport_name = spec["transport"]
        target, terr = _resolve_target(spec)
        if terr:
            result.routes_fired.append(RouteResult(
                channel=ch_slug, transport=transport_name,
                status="failed", error=terr,
            ))
            continue
        try:
            trans = _load_transport(transport_name, xdir)
        except Exception as e:
            result.routes_fired.append(RouteResult(
                channel=ch_slug, transport=transport_name,
                status="failed", error=f"transport load error: {e}",
            ))
            continue

        t0 = time.time()
        try:
            http_status, http_err = await trans.send(target, rendered)
            elapsed = int((time.time() - t0) * 1000)
            if 200 <= http_status < 300:
                result.routes_fired.append(RouteResult(
                    channel=ch_slug, transport=transport_name,
                    status="sent", http_status=http_status, elapsed_ms=elapsed,
                ))
            else:
                result.routes_fired.append(RouteResult(
                    channel=ch_slug, transport=transport_name,
                    status="failed", http_status=http_status,
                    error=http_err, elapsed_ms=elapsed,
                ))
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000)
            result.routes_fired.append(RouteResult(
                channel=ch_slug, transport=transport_name,
                status="failed", error=str(e)[:300], elapsed_ms=elapsed,
            ))

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_data(items: list[str]) -> dict:
    """Convert --data k=v --data k2=v2 → {k: v, k2: v2}.
    JSON values supported via prefix: --data list:='[1,2,3]'."""
    out: dict = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"--data value missing '=': {item}")
        k, v = item.split("=", 1)
        if k.endswith(":"):
            try:
                out[k[:-1]] = json.loads(v)
            except json.JSONDecodeError as e:
                raise SystemExit(f"--data {k} JSON parse failed: {e}")
        else:
            out[k] = v
    return out


def main() -> int:
    p = argparse.ArgumentParser(prog="notify.py", description=__doc__)
    sp = p.add_subparsers(dest="cmd", required=True)

    p_send = sp.add_parser("send", help="render + dispatch one event")
    p_send.add_argument("event_type")
    p_send.add_argument("--routes", required=True, help="path to routes.yaml")
    p_send.add_argument("--data", action="append", default=[],
                        help="payload k=v (use k:= for JSON values)")
    p_send.add_argument("--tenant", help="tenant slug for routing override")
    p_send.add_argument("--template-dir", help="override templates/")
    p_send.add_argument("--transports-dir", help="override transports/")
    p_send.add_argument("--dry-run", action="store_true",
                        help="render + log; no HTTP")
    p_send.add_argument("--json-out", action="store_true",
                        help="emit result JSON to stdout")

    p_render = sp.add_parser("render", help="render a template; no send")
    p_render.add_argument("event_type")
    p_render.add_argument("--template-dir", default=str(DEFAULT_TEMPLATE_DIR))
    p_render.add_argument("--data", action="append", default=[])

    p_val = sp.add_parser("validate-routes", help="lint a routes.yaml")
    p_val.add_argument("routes")

    p_ver = sp.add_parser("version", help="print harness-notify version")

    args = p.parse_args()

    if args.cmd == "version":
        print(HARNESS_VERSION)
        return 0

    if args.cmd == "validate-routes":
        try:
            load_routes(args.routes)
            print(f"OK {args.routes}")
            return 0
        except Exception as e:
            print(f"FAIL {args.routes}: {e}", file=sys.stderr)
            return 1

    if args.cmd == "render":
        try:
            data = _parse_data(args.data)
            out = render_event(
                args.event_type, data,
                template_dir=Path(args.template_dir),
            )
            print(out)
            return 0
        except Exception as e:
            print(f"FAIL render: {e}", file=sys.stderr)
            return 1

    if args.cmd == "send":
        try:
            data = _parse_data(args.data)
            result = asyncio.run(notify(
                event_type=args.event_type,
                routes_path=args.routes,
                payload=data,
                tenant_slug=args.tenant,
                template_dir=Path(args.template_dir) if args.template_dir else None,
                transports_dir=Path(args.transports_dir) if args.transports_dir else None,
                dry_run=args.dry_run,
            ))
        except Exception as e:
            print(f"FAIL send: {e}", file=sys.stderr)
            return 1

        if args.json_out:
            print(json.dumps(asdict(result), indent=2, ensure_ascii=False, default=str))
        else:
            if result.error:
                print(f"ERROR: {result.error}", file=sys.stderr)
            for r in result.routes_fired:
                marker = "✓" if r.status == "sent" else "✗"
                print(f"  {marker} {r.channel} via {r.transport}: {r.status}"
                      + (f" (http {r.http_status})" if r.http_status else "")
                      + (f" — {r.error}" if r.error else ""))

        if result.error:
            return 1
        statuses = [r.status for r in result.routes_fired]
        if not statuses:
            return 1
        if all(s == "sent" for s in statuses):
            return 0
        if any(s == "sent" for s in statuses):
            return 2
        return 3

    return 1


if __name__ == "__main__":
    sys.exit(main())
