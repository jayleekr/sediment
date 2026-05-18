# Ralph Learnings

> Append-only memory across iterations. Inspired by self-improving agent pattern
> (AddyOsmani, MindStudio 2026): each entry is a lesson learned from a stuck
> moment. Ralph reads tail-100 lines every iteration to avoid repeating mistakes.
>
> Format:
> ```
> [ISO_TIMESTAMP] medic-iter=N pattern=<name> detail=<one-line>
>   cause:    <hypothesis>
>   fix:      <what was done>
>   prevent:  <how future iters avoid this>
> ```
>
> Patterns: service_down, stalled_task, repeating_error, state_drift,
> score_regression, journal_overflow, rate_limit, cost_overrun

---

[2026-05-05T22:00:00Z] init pattern=bootstrap detail=ralph_started
  cause:    new project
  fix:      n/a (clean start)
  prevent:  n/a
