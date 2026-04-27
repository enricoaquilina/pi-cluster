# Pi-Cluster Project Rules

## Push Policy

Direct push to master allowed ONLY for:
- Docs/README changes
- Frontend-only (mission-control/frontend/)
- Single-file script fix
- Test-only changes

All other changes MUST go through a PR:
- Backend Python (mission-control/backend/)
- Ansible playbooks (playbooks/)
- CI workflows (.github/workflows/)
- Docker Compose files
- Hooks or pre-commit config
- Changes touching 4+ files

When in doubt, use a PR. PRs get CI + AI review automatically.

## CI Debugging

Test workflow changes locally with `act` before pushing. If not possible, use a single PR with fixup commits — don't create 12 PRs in 50 minutes.

## Squash Merge Safety

Before merging a PR where claude-fix pushed commits, verify nothing dropped:
```
git diff origin/master...feature-branch | wc -l
```
Should match the PR diff size. If not, changes were lost in squash.

## Feature PRs

All PRs (including `feat:`) auto-merge when CI Gate + Review Gate + AI Security Verdict pass. GPT-5.4 blocks merge on critical findings (>=9/10). Post-deploy smoke test + rollback provides the safety net.
