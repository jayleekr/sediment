"""Connector ABC — source-agnostic capture interface.

Every concrete connector (Discord, Slack, Notion, GitHub, ...) implements
this shape so the Collection AI Agent can schedule them uniformly. The
events table stores normalized output; the source-specific payload is
preserved in the `payload` JSONB column for traceback.

The watermark model is "latest message_id seen per (tenant, resource)" —
NOT "timestamp >= since", because Discord/Slack/etc. all key on snowflakes
or message IDs for ordering, and clock skew at the source is a recurring
gotcha. The `tenant_connector_state` table (TBD migration) stores it.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Resource:
    """A discoverable container at the source — Discord channel, Slack
    channel, Notion database, GitHub repo, etc. Connector reports these
    via `list_resources()` so the tenant can opt resources in/out via
    `tenant_connectors.config`."""
    id: str                 # source-native id (snowflake, slug, etc.)
    name: str               # display name, e.g. "meeting-notes"
    kind: str               # "channel" | "thread" | "space" | "repo" | ...
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedEvent:
    """The shape the connector hands back; the caller inserts into
    `events` with these fields. `payload` includes source-native fields
    that the distill strategy may need (author name, attachments URLs,
    upstream tool metadata for meeting summaries, etc.)."""
    source: str              # "discord" | "slack" | ...
    kind: str                # "message" | "transcript" | "page_edit" | ...
    external_id: str         # source-native unique id (for dedup)
    ts: datetime             # source timestamp (UTC)
    payload: dict[str, Any]  # raw + enrichment
    member_external_id: str | None = None  # for member resolution
    resource_id: str | None = None         # which Resource this came from


class ConnectorABC(abc.ABC):
    """Contract every source connector implements."""

    # Identifier matching `events.source` column.
    source_name: str = ""

    @abc.abstractmethod
    async def list_resources(self) -> list[Resource]:
        """Enumerate resources available to this connector (e.g. all
        channels the bot can read). Used during onboarding to populate
        `tenant_connectors.config.resources`."""

    @abc.abstractmethod
    async def fetch_since(
        self,
        resource: Resource,
        after_external_id: str | None,
        limit: int = 100,
    ) -> list[NormalizedEvent]:
        """Pull events newer than the given watermark. Returns oldest-first
        so callers can advance the watermark to the LAST item's external_id.

        Implementations MUST be idempotent: re-running with the same
        watermark must not produce duplicate events. The events table's
        unique constraint on (tenant_id, source, external_id) is the
        safety net, but connectors should not rely on it.
        """

    async def aclose(self) -> None:
        """Optional: release HTTP clients, sockets, etc."""
        return None
