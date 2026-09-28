import Link from "next/link";
import { Surface } from "../components/ui";

export default function PricingPage() {
  // Tenant-tier flat pricing (NOT per-seat). Internal rationale + cost math
  // lives in vault artifact `internal/pricing-strategy` (members-only).
  // See library page → search "pricing strategy".
  //
  // Funnel design (2026-05-21 update — Free tier added per Jay's feedback):
  //   Free  → on-ramp ("try it on one channel, no card")
  //   Solo  → bridge for 2-3 person studio that outgrew Free
  //   Studio → lead tier for teams of 5-15 (the cash cow)
  //   Pro   → upmarket 20-30 person agencies
  //   Ent   → custom for 50+ regulated orgs
  const tiers = [
    {
      name: "Free",
      price: "$0",
      cadence: "forever",
      sub: "No credit card required",
      highlights: [
        "1 channel from any source",
        "100 events / month",
        "7-day decision history",
        "50 RAG chat queries / month",
        "Community support",
      ],
      cta: "Get started",
      featured: false,
    },
    {
      name: "Solo",
      price: "$19",
      cadence: "/mo",
      sub: "₩25,000/mo",
      highlights: [
        "1 workspace, 3 channels",
        "1,000 events / month",
        "Decisions + actions extraction",
        "Source citations",
        "Email support",
      ],
      cta: "Start free trial",
      featured: false,
    },
    {
      name: "Studio",
      price: "$99",
      cadence: "/mo flat",
      sub: "₩130,000/mo · up to 10 seats",
      highlights: [
        "1 workspace, 10 channels",
        "10,000 events / month",
        "RAG chat with citations",
        "Member attribution + roles",
        // 슬래시 구분자는 카드 폭에서 끊기지 않는 한 덩어리라 넘쳐 잘렸다.
        // 쉼표+공백은 자연스러운 줄바꿈 지점을 만들고 영어 조판으로도 옳다.
        "Multi-source connectors (Discord, Slack, Notion, GitHub)",
      ],
      cta: "Start 14-day trial",
      featured: true,
      badge: "인기",
    },
    {
      name: "Pro",
      price: "$299",
      cadence: "/mo flat",
      sub: "₩400,000/mo · up to 30 seats",
      highlights: [
        "Multi-workspace",
        "50,000 events / month",
        "All connectors unlocked",
        "PII redaction + audit log",
        "Google SSO",
        "Priority support",
      ],
      cta: "Start trial",
      featured: false,
    },
    {
      name: "Enterprise",
      price: "Contact",
      cadence: "",
      sub: "From $999/mo",
      highlights: [
        "Unlimited workspaces",
        "Dedicated instance + data residency",
        "SAML / Okta SSO",
        "Custom prompts per tenant",
        "SLA 99.9%",
        "On-prem option",
      ],
      cta: "Talk to us",
      featured: false,
    },
  ];

  // 10-seat math comparison — the critical conversion lever (vs Glean / Notion / etc).
  const compare = [
    { tool: "Glean", price: "$500-800", note: "5-8x more" },
    { tool: "Otter Business", price: "$200-300", note: "2-3x more" },
    { tool: "Notion AI Business", price: "$200", note: "2x more" },
    { tool: "Fireflies Business", price: "$190", note: "2x more" },
    { tool: "Granola Business", price: "$140", note: "1.4x more" },
    { tool: "Sediment Studio", price: "$99 flat", note: "—", us: true },
  ];

  return (
    <div className="space-y-12">
      <div className="text-center">
        <h2 className="font-display text-4xl font-semibold tracking-tight text-ink">Pricing</h2>
        <p className="mt-2 text-ink-2">
          Flat per-team pricing — not per-seat. Add members without penalty.
        </p>
        <p className="mt-1 text-sm text-ink-3">
          Start free. No credit card required. Upgrade only when your team outgrows the free quota.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        {tiers.map((t) => (
          // flex-col + 아래 CTA 의 mt-auto 가 한 쌍이다. 이게 없으면 카드마다
          // 항목 수가 달라 CTA 가 세로로 제각각 서고, 5장이 나란히 놓였을 때
          // 그 어긋남이 가장 먼저 눈에 띈다.
          <Surface
            key={t.name}
            as="div"
            className={`relative flex flex-col p-6 ${t.featured ? "ring-2 ring-accent" : ""}`}
          >
            {t.featured && t.badge && (
              <span className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-accent px-3 py-0.5 text-xs font-medium text-paper">
                {t.badge}
              </span>
            )}
            <h3 className="font-display text-lg font-semibold text-ink">{t.name}</h3>
            <div className="mt-3 flex items-baseline gap-1">
              <span className="font-display text-3xl font-semibold text-ink">{t.price}</span>
              <span className="text-sm text-ink-3">{t.cadence}</span>
            </div>
            {t.sub && (
              <p className="mt-1 text-xs text-ink-3">{t.sub}</p>
            )}
            <ul className="mt-4 space-y-2 text-sm">
              {t.highlights.map((h) => (
                <li key={h} className="flex gap-2">
                  <span className="shrink-0 text-accent">✓</span>
                  {/* 넘침에는 원인이 둘이었다. (1) flex 자식의 기본 min-width 가
                      auto 라 칸이 좁아져도 줄어들지 못한다 → min-w-0. (2) 그래도
                      줄바꿈 지점이 없는 긴 토큰은 넘친다 → 구분자를 슬래시에서
                      쉼표로 바꿨다(위 tiers 참고). break-words 는 쓰지 않는다.
                      단어 중간("Notio / n")을 끊어 조판이 무너지기 때문이다. */}
                  <span className="min-w-0">{h}</span>
                </li>
              ))}
            </ul>
            {/* mt-auto 는 이 래퍼가 갖는다. Link 자신에게 주면 버튼 안쪽
                여백과 바깥 간격이 한 속성에 섞여 조정이 어려워진다. */}
            <div className="mt-auto pt-6">
              <Link
                href={`/sediment/onboard?tier=${encodeURIComponent(t.name.toLowerCase())}`}
                className={`block w-full rounded-md px-4 py-2.5 text-center text-sm font-medium transition-colors ${
                  t.featured
                    ? "bg-accent text-paper hover:bg-accent-ink"
                    : "border border-rule bg-paper-2 text-ink hover:border-rule-2 hover:bg-card-2"
                }`}
              >
                {t.cta}
              </Link>
            </div>
          </Surface>
        ))}
      </div>

      {/* 10-seat math — main conversion lever per pricing strategy memo */}
      <div className="rounded-md border border-rule bg-paper-2/50 p-6">
        <h3 className="font-display text-lg font-semibold text-ink">
          10-seat team monthly cost — Sediment vs alternatives
        </h3>
        <p className="mt-1 text-sm text-ink-2">
          Most SaaS knowledge tools charge per-seat. A growing 10-person team gets penalized for growth.
          We charge flat per workspace — your bill stays the same as you scale.
        </p>
        <table className="mt-4 w-full text-sm">
          <caption className="sr-only">Ten seat monthly cost comparison</caption>
          <thead className="border-b border-rule-2 text-left font-mono text-[11px] uppercase tracking-[0.12em] text-ink-3">
            <tr>
              <th scope="col" className="py-2 pr-4">Tool</th>
              <th scope="col" className="py-2 pr-4">10-seat monthly</th>
              <th scope="col" className="py-2">vs Studio $99</th>
            </tr>
          </thead>
          <tbody>
            {compare.map((row) => (
              <tr
                key={row.tool}
                className={`border-t border-rule ${row.us ? "bg-ochre-soft/40 font-medium" : ""}`}
              >
                <td className="py-2 pr-4">{row.tool}</td>
                <td className="py-2 pr-4">{row.price}</td>
                <td className="py-2 text-ink-2">{row.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-3 text-xs text-ink-3">
          Comparison prices reflect Business / Team tier list pricing as of May 2026.
          Glean and Otter source verified via vendor sales pages.
        </p>
      </div>

      <p className="text-center text-xs text-ink-3">
        Pricing locked 2026-05-20. Stripe billing + quota enforcement land alongside first paid customer.
      </p>
    </div>
  );
}
