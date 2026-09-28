import type { CSSProperties, ElementType, FormEventHandler, ReactNode } from "react";

// ── Editorial-Archive shared primitives ──────────────────────────────────
// Surfaces read like sheets of stock on a desk: warm card, hairline rule,
// a low soft shadow. Headers borrow the typographic voice (Fraunces display).

export function Surface({
  children,
  className = "",
  as = "section",
  onSubmit,
  style,
}: {
  children: ReactNode;
  className?: string;
  // "form" 은 채팅 작성란 때문에 열어뒀다. 그 폼은 Surface 레시피를 손으로
  // 다시 조립하고 있었는데, 태그가 다르다는 이유만으로 프리미티브를 못 쓰는
  // 것은 프리미티브 쪽 결함이지 호출부 잘못이 아니다.
  as?: "section" | "aside" | "div" | "form";
  // form 으로 쓸 때만 의미가 있다. 전체 props 를 퍼뜨리지 않는 이유는 태그마다
  // 이벤트 핸들러 타입이 달라 유니온에서 충돌하기 때문이고, 그 편이 프리미티브를
  // 작게 유지하기도 한다.
  onSubmit?: FormEventHandler<HTMLFormElement>;
  // 사실상 모션용 통로다. `.enter-item` 같은 클래스는 stagger 지연을 `--i`
  // 커스텀 프로퍼티로 읽는데, 그 값은 렌더 시점에만 알 수 있어서 클래스가
  // 아니라 인라인 스타일로 넘길 수밖에 없다. 색·간격을 여기로 우회시키라는
  // 뜻은 아니다 — 그건 className 과 토큰의 몫이다.
  style?: CSSProperties;
}) {
  const Component = as as ElementType;
  return (
    <Component
      className={`rounded-md border border-rule bg-card shadow-[0_1px_2px_rgba(34,30,22,0.04),0_8px_24px_-16px_rgba(34,30,22,0.25)] ${className}`}
      onSubmit={onSubmit}
      style={style}
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
