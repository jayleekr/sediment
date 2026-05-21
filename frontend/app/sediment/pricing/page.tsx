export default function PricingPage() {
  // Tenant-tier flat pricing (NOT per-seat). Internal rationale + cost math
  // lives in vault artifact `internal/pricing-strategy` (members-only).
  // See library page → search "pricing strategy".
  const tiers = [
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
        "Multi-source connectors (Discord/Slack/Notion/GitHub)",
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
        <h2 className="text-3xl font-bold">Pricing</h2>
        <p className="mt-2 text-neutral-600">
          Flat per-team pricing — not per-seat. Add members without penalty.
        </p>
        <p className="mt-1 text-sm text-neutral-500">
          14-day free trial on Studio &amp; Pro. No card required.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
        {tiers.map((t) => (
          <div
            key={t.name}
            className={`relative rounded-xl border bg-white p-6 ${
              t.featured ? "ring-2 ring-blue-600 shadow-lg" : "shadow-sm"
            }`}
          >
            {t.featured && t.badge && (
              <span className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-blue-600 px-3 py-0.5 text-xs font-medium text-white">
                {t.badge}
              </span>
            )}
            <h3 className="text-lg font-semibold">{t.name}</h3>
            <div className="mt-3 flex items-baseline gap-1">
              <span className="text-3xl font-bold">{t.price}</span>
              <span className="text-sm text-neutral-500">{t.cadence}</span>
            </div>
            {t.sub && (
              <p className="mt-1 text-xs text-neutral-500">{t.sub}</p>
            )}
            <ul className="mt-4 space-y-2 text-sm">
              {t.highlights.map((h) => (
                <li key={h} className="flex gap-2">
                  <span className="text-blue-600">✓</span>
                  <span>{h}</span>
                </li>
              ))}
            </ul>
            <button
              className={`mt-6 w-full rounded px-4 py-2 text-sm ${
                t.featured
                  ? "bg-blue-600 text-white hover:bg-blue-700"
                  : "bg-neutral-100 text-neutral-900 hover:bg-neutral-200"
              }`}
            >
              {t.cta}
            </button>
          </div>
        ))}
      </div>

      {/* 10-seat math — main conversion lever per pricing strategy memo */}
      <div className="rounded-xl border bg-neutral-50 p-6">
        <h3 className="text-lg font-semibold">
          10-seat team monthly cost — Sediment vs alternatives
        </h3>
        <p className="mt-1 text-sm text-neutral-600">
          Most SaaS knowledge tools charge per-seat. A growing 10-person team gets penalized for growth.
          We charge flat per workspace — your bill stays the same as you scale.
        </p>
        <table className="mt-4 w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-neutral-500">
            <tr>
              <th className="py-2 pr-4">Tool</th>
              <th className="py-2 pr-4">10-seat monthly</th>
              <th className="py-2">vs Studio $99</th>
            </tr>
          </thead>
          <tbody>
            {compare.map((row) => (
              <tr
                key={row.tool}
                className={`border-t ${row.us ? "bg-blue-50 font-medium" : ""}`}
              >
                <td className="py-2 pr-4">{row.tool}</td>
                <td className="py-2 pr-4">{row.price}</td>
                <td className="py-2 text-neutral-600">{row.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-3 text-xs text-neutral-500">
          Comparison prices reflect Business / Team tier list pricing as of May 2026.
          Glean and Otter source verified via vendor sales pages.
        </p>
      </div>

      <p className="text-center text-xs text-neutral-500">
        Pricing locked 2026-05-20. Stripe billing + quota enforcement land alongside first paid customer.
      </p>
    </div>
  );
}
