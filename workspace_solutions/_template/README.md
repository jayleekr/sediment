# `workspace_solutions/<customer-slug>/` — Enterprise dedicated package template

Per-customer dedicated instance template (Phase 10+). Mirrors AIT's
`ait_solutions/{marston, rp129, wolverine}/` pattern — one directory per
enterprise customer, packaged into a separate Docker image and deployed to a
customer-specific cloud account or on-prem.

When to use:
- Customer requires data residency (e.g., KR region) outside our shared infra
- Customer requires SAML/SSO + custom audit log retention
- Customer requires on-prem / air-gapped deployment
- Customer's vault size exceeds shared-tier capacity

Layout (when populated):
```
workspace_solutions/<customer>/
├── tenant.yaml              # display_name, region, branding, feature_flags
├── docker-compose.yml       # customer-pinned versions
├── infra/
│   ├── terraform/           # cloud-init + per-region module
│   └── helm/                # k8s chart for on-prem
├── workspace_mcp_overrides/ # custom MCP tools (e.g., customer's CMS connector)
├── lens_overrides/          # custom philosophical framework if customer wants
├── prompt_overrides/        # tone-of-voice / persona overrides
└── eval/                    # customer SLA validation harness
```

To create a new dedicated package:
```bash
cp -R workspace_solutions/_template workspace_solutions/<customer-slug>
# Edit tenant.yaml + branding
make deploy-dedicated CUSTOMER=<customer-slug>
```

Deployment patterns:
- **HypeProof Cloud KR** — Naver Cloud / AWS Seoul, single tenant per cluster
- **HypeProof Cloud EU** — AWS Frankfurt, single tenant per cluster
- **Customer Cloud** — customer's AWS/GCP/Azure account, helm chart
- **Air-gapped** — bundled image + license key, customer self-host
