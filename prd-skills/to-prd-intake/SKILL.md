---
name: to-prd-intake
description: Wrapper for to-prd that first performs requirement intake, dedupe, and ownership analysis before creating or updating a PRD. Use instead of direct to-prd when the user asks to create a PRD, write a requirement document, or turn a request into a requirement.
---

# To PRD Intake

Use this wrapper before `to-prd`. Do not create a new PRD until the intake decision says `new-prd`.

## Intake First

Before calling `to-prd`, inspect existing requirements — invoke the `requirements-dashboard` skill to get current tracker state.

Then read likely related files under `.scratch/*/PRD.md` and `.scratch/*/issues/*.md`.

Check:

- Same business area: reports, Feishu cards, OA crawler, Liu after-sales sheet, workflow state, prompt feedback, wiki/RAG.
- Same user goal or actor.
- Same acceptance criteria or touched modules.
- Existing PRD that can own the request.
- Existing issue that already covers the request.
- External-system scope that should not become repo implementation work.

## Decision

Produce this short decision before writing files:

```md
## Requirement Intake Decision

Recommended action: new-prd | update-existing-prd | add-child-issues | update-existing-issue | duplicate | external-system

Canonical owner:
- PRD: `.scratch/.../PRD.md` or `new`
- Issue: `.scratch/.../issues/NN-...md` or `none`

Reason:
- ...

Next step:
- call `to-prd`
- call `to-issues`
- edit existing PRD
- edit existing issue
- do not create new tracker item
```

## When To Call `to-prd`

Call the original `to-prd` only when all are true:

- The request introduces a new user goal or workflow.
- No existing PRD can naturally own it.
- It likely needs multiple independently verifiable issues.
- It is not just config, test coverage, bugfix, migration, residual audit, or external scheduler setup.

After `to-prd`, add or preserve:

```md
Status: ready-for-agent

## Requirement metadata

- Area: <area>
- Relation: canonical
- Canonical: true
- Related: <paths or none>
```

Then invoke the `requirements-dashboard` skill (with `--strict`) to validate the tracker is clean.

## Preferred Alternatives

- Existing PRD owns it: update that PRD, then use `to-issues-intake`.
- Existing issue covers it: update the issue instead of creating a PRD.
- External system: document as out-of-scope/residual; do not block AI-verifiable repo work.
- Duplicate: point to canonical PRD/issue and do not create a new PRD.
