---
name: correct
description: "Find the mistakes agents keep repeating in this repo and make each one impossible. Try architecture first, then types, then a lint whose error names the fix, then a test, and write docs last. Prove each check fails on a real past mistake. Repeat this each time the operator corrects you. Use for /correct."
disable-model-invocation: true
---

> Provenance: forked from `backnotprop/pstack@0.15.36` (`skills/correct`; MIT, Copyright (c) 2026 Lauren Tan — see `LICENSE`).
> Changes: every rule must name its enforcer — a rule with none is a repeat mistake (`待固化`), and writing the rule in a document alone never counts as done;
> added the three-layer complementary model (inone-memory cross-project correction memory, experience-kb project experience docs, this skill encoding corrections into repo structure);
> "one PR" rewritten to the target repo's delivery convention (a local commit is a delivery; never self-push); harness-neutral wording. Re-check upstream on later revisions.

# Correct

The operator keeps correcting agents in this repo for the same mistakes. Change the repo so the next agent can't make them.

Assume every contributor is an agent that sees only the files it opened, copies the nearest example, and takes the shortest path that compiles. Design the repo so a change that looks right from one file is right for the whole repo.

## Three complementary layers

These layers overlap on purpose; use all three, each for what it is good at:

- **inone-memory** — cross-project correction memory (layer 1): a correction that generalizes beyond this repo is remembered once and reaches every project.
- **experience-kb** — project experience docs (layer 2): guidance specific to this project is written down where the next agent will read it.
- **correct** — this skill (layer 3): encode the correction into the repo structure itself (architecture, types, lint, tests) so the mistake cannot be repeated.

Prefer the highest layer that can carry the correction; documents are the last resort, not the first.

## Find the mistake classes

First, read recent commits, reverts, review comments, agent instruction files, and comments that explain workarounds. Group the mistakes into classes. A class counts once the same mistake has happened at least twice; a one-off is an incident, not a class.

## Fix each class at the highest level that works

1. **Eliminate it with architecture.** Give each piece of state one owner and each task one supported way. Hide internals so the wrong import fails. Replace hand-synced lists with one source of truth. Delete old ways and dead code an agent would copy.
2. **Enforce it with types so the bad state can't be written.** Make the invalid program fail to compile or type-check, not merely fail review.
3. **Add a lint or CI check whose error names the fix.** The message must name the file, type, or function to use instead, so the offending agent can act on the error alone. If the pattern is already common, fail only when a change adds more.
4. **Test the behavior.** Fix or delete any test that still passes when the behavior it checks breaks, or that fails when only the internals change.
5. **Write docs or agent rules last, only for judgment calls.** Nothing fails when an agent skips them.

## Every rule has an enforcer

A rule with no enforcer is a repeat mistake waiting to happen, not a finished fix. For every rule in the table, name the architecture, type, lint, or test that enforces it. When no higher level can carry it, either raise it to the highest level that works, or mark it explicitly `待固化` (pending hardening) and say which level is missing and why. Writing the rule in a document alone never counts as done.

## Fix and prove

Then fix the most frequent classes now, one delivery each, per the target repo's delivery convention: a local commit is a delivery, never self-push, and a blocked or unclean change delivers nothing. Prove each new check fails on a real past mistake: replay the historical error — the actual diff, revert, or failing input — and show the check catches it. Run the same command locally and in CI. Exceptions go on the offending line with a reason, an expiry date, and a human's approval.

## Keep the rule table

Last, keep a table in the agent instruction file that pairs each rule with what enforces it (rule ↔ enforcer). When the operator corrects you, fix the mistake and add the rule. If the rule was already there and nothing enforces it, that's a repeat, so fix it at the highest level in the same change, or mark it `待固化`. Drop a rule once its mistake can't happen.

**Reply:** each class with its evidence (where the repeated mistake shows up), the level you picked, why a higher level didn't work, and the enforcer — or the `待固化` reason.
