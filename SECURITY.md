# Security Policy

## Reporting

Do not open a public issue for secrets, auth bypasses, cross-tenant data leaks,
token leaks, webhook forgery, or deploy credential problems.

Send the report directly to the maintainers:

- GitHub: `@jayleekr`
- GitHub: `@JeHyeong2`

Include the affected endpoint, branch or commit, reproduction steps, and whether
any credential or tenant data may have been exposed. Never include full secret
values.

## Secret Handling

- Do not commit `.env`, private keys, JWT secrets, Fly/Vercel tokens, webhook
  secrets, or provider API keys.
- Production credentials must live in GitHub Secrets, Fly secrets, Vercel env
  vars, or provider-managed secret stores only.
- If a secret reaches git, rotate it first, then decide whether history cleanup
  is needed.

## Branch Policy

`main` should only receive changes through PR review and CI. Public users may
open PRs, but only HypeProof members or approved automation may merge or deploy.
Cross-tenant RLS tests and secret-diff checks are release blockers.
