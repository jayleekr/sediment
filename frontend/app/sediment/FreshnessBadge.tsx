"use client";

import { useEffect, useState } from "react";
import { getFreshness, getToken, type Freshness } from "./lib/api";

// Makes a stale vault VISIBLE in the header instead of silently rotting —
// the #1 thing that kills dogfood (ACTIVATION_ENGINE.md P1 freshness metric).
export default function FreshnessBadge() {
  const [f, setF] = useState<Freshness | null>(null);

  useEffect(() => {
    let stopped = false;
    async function tick() {
      if (!getToken()) return; // freshness endpoint requires auth
      try {
        const r = await getFreshness();
        if (!stopped) setF(r);
      } catch {
        /* ignore — badge is non-critical */
      }
    }
    tick();
    const iv = setInterval(tick, 60_000);
    return () => {
      stopped = true;
      clearInterval(iv);
    };
  }, []);

  if (!f) return null;

  const s = f.seconds_ago;
  const issueCount = f.violations?.length ?? 0;
  const label =
    f.last_ingest_ts == null
      ? "vault: never"
      : s == null
      ? "vault: ?"
      : s < 3600
      ? `vault ${Math.max(1, Math.round(s / 60))}m ago`
      : s < 86400
      ? `vault ${Math.round(s / 3600)}h ago`
      : `vault ${Math.round(s / 86400)}d ago`;

  return (
    <span
      title={
        issueCount
          ? `violations: ${f.violations?.join(", ")}`
          : f.last_ingest_ts ?? f.note ?? ""
      }
      className={`font-mono text-[11px] uppercase tracking-[0.14em] ${
        f.stale ? "text-ochre" : "text-sage"
      }`}
    >
      {f.stale ? "⚠ " : "● "}
      {label}
      {issueCount ? ` · ${issueCount}` : ""}
    </span>
  );
}
