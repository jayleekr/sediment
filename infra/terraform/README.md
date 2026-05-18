# Terraform — Phase 9+ shared infra, Phase 10+ per-customer

Shared SaaS production deployment (Phase 9+):

```
infra/terraform/shared/
├── main.tf            # Vercel + Supabase + Cloudflare
├── variables.tf
└── outputs.tf
```

Per-customer enterprise deployment (Phase 10+):

```
infra/terraform/dedicated/
├── modules/
│   ├── ec2-cluster/   # Postgres + Redis + service VMs
│   ├── cloudfront/
│   └── waf/
└── envs/
    └── <customer-slug>.tfvars
```

> Stub for now — actual modules land when first paying customer signs up.
> The point is the directory exists so Phase 10 can drop straight in.
