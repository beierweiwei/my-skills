---
name: to-issues-intake
description: Wrapper for to-issues that first performs requirement intake, dedupe, parent selection, and slice ownership analysis before creating issues. Use instead of direct to-issues when splitting a PRD/plan/request into implementation issues.
---

# To Issues Intake

Use this wrapper before `to-issues`. Do not create new issues until the intake decision identifies the parent PRD and confirms they are not duplicates.

## Intake First

Before calling `to-issues`, inspect current tracker state:

```bash
.venv/bin/python scripts/requirements_status.py --format json
```

Read the candidate parent PRD and related issues. If no parent was provided, search `.scratch/*/PRD.md` and pick the best owner before splitting.

Check each proposed slice for:

- Existing issue already covers the same acceptance criteria.
- Slice should merge into an existing issue.
- Slice should be a child of an existing PRD.
- Slice is external-system setup and should not block repo implementation.
- Slice has a different verification mode from a related issue and should stay separate.

## Decision

Produce this short decision before writing files:

```md
## Issue Intake Decision

Parent PRD: `.scratch/.../PRD.md`

Proposed action:
- create-child-issues
- update-existing-issues
- merge-with-existing
- mark-duplicate
- record-external-system-residual

Related existing issues:
| Path | Relation | Decision |
|---|---|---|
| `.scratch/.../issues/NN-...md` | duplicate-risk | update instead of create |

Verification split:
- AI-verifiable slices: ...
- Human/external slices: ...
```

## When To Call `to-issues`

Call the original `to-issues` only after:

- Parent PRD is identified.
- Duplicate issues are ruled out or explicitly marked.
- Each slice has one clear owner and can be verified independently.
- External-system tasks are separated from repo-local implementation tasks.

Prefer updating existing issues over creating duplicates.

## Issue Metadata

When creating or updating an issue, include:

```md
Status: ready-for-agent

## Requirement metadata

- Area: <area>
- Parent: `.scratch/.../PRD.md`
- Relation: child-of | follow-up | audit-derived | external-system | duplicate
- Canonical: true | false
- Canonical issue: `.scratch/.../issues/NN-...md` or none
- Verification: AI | Human | External
```

Use `Status: ready-for-human` only when the next action truly requires human decision, production operation, or business verification. Do not put external Hermes/OA/Feishu setup into an AI-verifiable code issue.

Then run:

```bash
.venv/bin/python scripts/requirements_status.py --strict
```
