<!-- gardener-constitution-proposal: v1 -->
# GARDENER.md

## Product Purpose

- Maintained repository: `hasagi33/bloomhub-be`.
- Review and edit this draft before merging; merging makes these rules source truth.

## Protected Modules

- `core/permissions.py/**` because it appears security-sensitive or business-critical.
- `core/management/**` because it appears security-sensitive or business-critical.

## Never-Touch Paths

- `**/.env*` because secrets must never be modified by Gardener.
- `**/secrets/**` because secret material requires human handling.
- `**/migrations/**` because database migrations require explicit review.

## Autonomous Fixes Allowed

- documentation updates
- lint and format-only changes
- dependency patch updates with passing checks

## Assisted Fixes Allowed

- tests
- dead code removal
- complexity reduction
- layer violation repair

## Advisory-Only Areas

- auth
- permissions
- tenancy
- payroll
- credentials
- security-sensitive code
- migrations

## Architecture Boundaries

- Runtime code must not import from `tests/**`.
- Presentation/API layers must not bypass service/domain modules for persistence behavior.

## Test Rules

- Run the repository's default backend test suite before backend changes.
- Run relevant targeted tests for changed modules.

## Ignored Paths

- `.repowise/**`
- `.venv/**`
- `node_modules/**`
- `dist/**`
- `build/**`
- `staticfiles/**`
- `media/**`

## Health Priorities

- Current entropy classification: `no_autonomy`.
- Current entropy score: `58.9`.
- Prefer focused, reviewable maintenance PRs.

## Trigger and PR Preferences

- Prefer small PRs scoped to one maintenance category.
- Do not auto-merge Gardener PRs.
