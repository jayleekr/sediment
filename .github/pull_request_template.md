<!--
HypeProof PR policy:
- Link the issue with Closes/Fixes/Resolves #...
- Keep main protected: no direct push.
- Never include secret values in screenshots, logs, or code.
-->

## What & why

<!-- What changed, and the problem it solves. -->

## Tested

<!-- Commands or manual checks run. -->

## Security / governance

- [ ] No secrets or credentials in the diff
- [ ] Tenant/user authorization boundaries are preserved
- [ ] RLS/cross-tenant tests are updated when auth or data access changes
- [ ] No workflow grants write/deploy permissions to external PR or comment triggers
- [ ] Branch protection / required checks remain compatible with this change

Closes #
