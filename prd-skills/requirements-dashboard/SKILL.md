---
name: requirements-dashboard
description: Maintains local .scratch PRD/issue status hygiene and generates a requirements progress dashboard. Use when creating or cleaning PRDs/issues, after to-prd/to-issues, or when the user asks for requirement progress, blockers, confirmations, AI verification, or human verification.
---

# Requirements Dashboard

Use this skill as the follow-up layer for `to-prd` and `to-issues` in this repo.

## Status Contract

Every `.scratch/<feature>/PRD.md` and `.scratch/<feature>/issues/*.md` starts with:

```md
Status: ready-for-agent
```

Allowed statuses:

- `needs-triage` — not evaluated yet.
- `needs-info` — missing user/domain information.
- `ready-for-agent` — AI/AFK agent can implement or verify with local evidence.
- `ready-for-human` — needs human decision, production operation, or business verification.
- `wontfix` — intentionally not actioned.
- `verified` — closed after verification. Verification can be AI or Human, but must include evidence.

Do not use `approved`, `done`, `partial`, `needs-implementation`, or `blocked-by-human-input` as top-level `Status`.

## Required Issue Sections

Each issue should include:

```md
## What to build
## Acceptance criteria
## Blocked by
```

When humans must act, add:

```md
## Human inputs needed

- [ ] ...
```

## Verification Records

For `Status: verified`, add a comment:

```md
## Comments

### Verification — YYYY-MM-DD

Verifier: AI
Result: passed
Evidence:
- `.venv/bin/pytest ...`
Safety:
- No real Feishu write was performed.
- No OA reply/send flow was triggered.
Residual risk:
- ...
```

Use `Verifier: Human` when the result depends on manual Feishu/OA/customer-service inspection.

## Dashboard Command

Run:

```bash
.venv/bin/python scripts/requirements_status.py
```

Use JSON for automation:

```bash
.venv/bin/python scripts/requirements_status.py --format json
```

Use strict mode before claiming the tracker is clean:

```bash
.venv/bin/python scripts/requirements_status.py --strict
```

Strict mode exits non-zero when PRDs/issues violate the format contract.

## Workflow

1. After `to-prd`, ensure the PRD has top-level `Status:` and the required PRD headings.
2. After `to-issues`, ensure every issue has top-level `Status:`, required sections, and concrete acceptance criteria.
3. Classify each issue:
   - AI-verifiable: keep or move to `ready-for-agent`; verify with local tests/dry-run/code audit before `verified`.
   - Human-verifiable: set `ready-for-human` and list exact human inputs.
   - Complete: set `verified` only with `Verifier`, `Result`, and `Evidence`.
4. Run `scripts/requirements_status.py --strict`.
5. Report counts: PRDs, issues by status, completed issues, blocked/human-confirmation items, and format issues.
