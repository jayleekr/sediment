import type { ReactNode } from "react";

// ── Editorial-Archive shared primitives ──────────────────────────────────
// Surfaces read like sheets of stock on a desk: warm card, hairline rule,
// a low soft shadow. Headers borrow the typographic voice (Fraunces display).

export function Surface({
  children,
  className = "",
  as = "section",
}: {
  children: ReactNode;
  className?: string;
  as?: "section" | "aside" | "div";
}) {
  const Component = as;
  return (
    <Component
      className={`rounded-md border border-rule bg-card shadow-[0_1px_2px_rgba(34,30,22,0.04),0_8px_24px_-16px_rgba(34,30,22,0.25)] ${className}`}
    >
      {children}
    </Component>
  );
}

export function SectionHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 className="font-display text-xl font-semibold leading-tight text-ink">
          {title}
        </h2>
        {description && (
          <p className="mt-1.5 max-w-prose text-[15px] leading-7 text-ink-2">
            {description}
          </p>
        )}
      </div>
      {action}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="rounded-md border border-dashed border-rule-2 bg-paper-2/60 px-5 py-6">
      <p className="font-display text-base font-semibold text-ink">{title}</p>
      <p className="mt-1.5 text-[15px] leading-7 text-ink-2">{description}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

// Small ledger-style marker. Mono, letter-spaced — reads like an editorial
// stamp rather than a SaaS pill. Tones map onto the archive palette.
export function TrustBadge({
  tone = "neutral",
  children,
  title,
}: {
  tone?: "neutral" | "success" | "warning" | "info";
  children: ReactNode;
  title?: string;
}) {
  const tones: Record<string, string> = {
    neutral: "border-rule-2 text-ink-2",
    success: "border-sage/50 text-sage",
    warning: "border-ochre/50 text-ochre",
    info: "border-accent/40 text-accent",
  };

  return (
    <span
      title={title}
      className={`inline-flex min-h-[1.4rem] items-center rounded-sm border px-2 font-mono text-[11px] uppercase tracking-[0.12em] ${tones[tone]}`}
    >
      {children}
    </span>
  );
}
