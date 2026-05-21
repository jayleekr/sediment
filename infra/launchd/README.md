# launchd plists for Sediment

**Empty as of 2026-05-21.** Scheduled jobs (daily-ingest, dream memory
consolidation) used to run as macOS LaunchAgents on Jay's laptop; both plists
were deleted because:

1. They referenced the pre-split monorepo path (`hypeproof/products/sediment/`)
   and stopped working after the 2026-05-18 repo split.
2. Scheduling moved into the Fly VM itself via the APScheduler daemon
   introduced in `b67b462` (Collection Agent cron). Running cron next to the
   app removes the laptop-on dependency.

Directory kept so the conventional install location is obvious if a
laptop-side schedule is ever needed again.

If you do need a new plist:
- Use the current repo layout (`services/sediment/` directly under the
  sediment repo root, NOT `products/sediment/`).
- Prefer adding the job to the in-VM APScheduler unless it genuinely needs
  the host filesystem.
