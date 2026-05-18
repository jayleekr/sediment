export default function PricingPage() {
  const tiers = [
    {
      name: "Free",
      price: "$0",
      cadence: "forever",
      highlights: ["3 seats", "1,000 queries/mo", "1 GB storage", "1 MCP integration", "Powered by HypeProof footer"],
      cta: "Sign up",
      featured: false,
    },
    {
      name: "Pro",
      price: "$20",
      cadence: "/seat/mo",
      highlights: ["Up to 25 seats", "500 queries/seat/mo", "10 GB storage", "3 MCP integrations", "Brand toggle off"],
      cta: "Start trial",
      featured: true,
    },
    {
      name: "Business",
      price: "$40",
      cadence: "/seat/mo",
      highlights: ["Up to 100 seats", "1,500 queries/seat/mo", "50 GB storage", "Unlimited MCP", "Custom domain", "Google SSO"],
      cta: "Start trial",
      featured: false,
    },
    {
      name: "Enterprise",
      price: "Contact",
      cadence: "",
      highlights: ["Unlimited seats", "Dedicated instance", "SAML/Okta SSO", "Audit log", "On-prem option", "SLA 99.9%"],
      cta: "Talk to us",
      featured: false,
    },
  ];

  return (
    <div className="space-y-8">
      <div className="text-center">
        <h2 className="text-3xl font-bold">Pricing</h2>
        <p className="mt-2 text-neutral-600">
          Free for small teams. Per-seat pricing for growing labs.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
        {tiers.map((t) => (
          <div
            key={t.name}
            className={`rounded-xl border bg-white p-6 ${
              t.featured ? "ring-2 ring-blue-600 shadow-lg" : "shadow-sm"
            }`}
          >
            <h3 className="text-lg font-semibold">{t.name}</h3>
            <div className="mt-3 flex items-baseline gap-1">
              <span className="text-3xl font-bold">{t.price}</span>
              <span className="text-sm text-neutral-500">{t.cadence}</span>
            </div>
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
                t.featured ? "bg-blue-600 text-white" : "bg-neutral-100 text-neutral-900"
              }`}
            >
              {t.cta}
            </button>
          </div>
        ))}
      </div>

      <p className="text-center text-xs text-neutral-500">
        Phase 7 stub — Stripe webhook + quota enforcement land when first paid customer signs up.
      </p>
    </div>
  );
}
