import type { ReactNode } from "react";

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
      className={`rounded-lg border border-neutral-200 bg-white shadow-sm ${className}`}
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
        <h2 className="text-base font-semibold text-neutral-950">{title}</h2>
        {description && (
          <p className="mt-1 text-sm leading-6 text-neutral-600">{description}</p>
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
    <div className="rounded-lg border border-dashed border-neutral-300 bg-neutral-50 px-4 py-5">
      <p className="text-sm font-semibold text-neutral-950">{title}</p>
      <p className="mt-1 text-sm leading-6 text-neutral-600">{description}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function TrustBadge({
  tone = "neutral",
  children,
  title,
}: {
  tone?: "neutral" | "success" | "warning" | "info";
  children: ReactNode;
  title?: string;
}) {
  const tones = {
    neutral: "bg-neutral-100 text-neutral-700",
    success: "bg-emerald-100 text-emerald-800",
    warning: "bg-amber-100 text-amber-800",
    info: "bg-blue-50 text-blue-700",
  };

  return (
    <span
      title={title}
      className={`inline-flex min-h-6 items-center rounded-full px-2.5 text-xs font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  );
}
