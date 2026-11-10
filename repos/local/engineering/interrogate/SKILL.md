---
name: interrogate
description: "Use for \"interrogate\", \"adversarial review\", \"multi-model review\", \"challenge this\", \"stress test this code\", \"find blind spots\", or \"tear this apart\". Adversaries try to break a change or design, and only findings proven by a run survive."
disable-model-invocation: true
---

> Provenance: forked from `backnotprop/pstack@0.15.36` (MIT).
> Changes: made self-contained — scope framing, adversary fan-out, verification, and output are inlined here instead of delegating to separately installed skills;
> economy-tier default — one adversary on the parent model (`inherit-parent`), no longer 3–4; escalate to two only when the diff touches a core module, an external/public API, or a data-security red line, and record the escalation in the report and the owning stream plan's `## Decisions Made`;
> a single adversary is the default, not a degraded fallback; parallel and cross-family adversaries are an optional upgrade, never a precondition; harness wording generalized to a subagent/worktree model. Re-check upstream on later revisions.

# Interrogate

Adversaries try to break a change or a design. A finding survives only when a run proves it. Report the findings. Do not apply fixes.

## Frame

The scope is what the user names. Otherwise it is the branch diff from its merge base with the base branch, through the working tree. Restate the intent in one paragraph from the user's message, the commits, and the PR description, and state it back before fanning out: what problem the change claims to solve, what behavior it must preserve, and which trade-offs it accepts. Ask the user when the intent is unclear; never attack a guessed intent.

## Fan out

Default to **one adversary** — the economy tier. Run it as one subagent on the parent model (omit `model` so the seat inherits the parent), or run the brief yourself when no subagent is available. It covers the attack angles in sequence: correctness; edge cases and failure modes; data and state; security and performance.

Before dispatching background subagents, declare a **timeout window** (for example 10–15 minutes). Do not end the turn while waiting. When the window expires with adversaries still outstanding, collect every returned finding first and record each missing angle as a **coverage gap**; never idle-wait or treat an unfinished angle as completed.

Escalate to **two adversaries** only when any of these holds:

- the diff touches a core module,
- the diff touches an external or public API,
- the diff touches a data-security red line (secrets handling, raw customer data, an authorization boundary).

Record the escalation and its reason in the interrogate report and in the owning stream plan's `## Decisions Made`; when the review does not run under a stream plan, say in the report where the decision was recorded. Parallel and cross-family adversaries are an upgrade, never a precondition.

With two adversaries, give each a distinct attack angle — for example correctness, edge cases and failure modes, data and state, security and performance — so they do not all fail the same way. Each adversary gets the scope, the intent, and this brief:

> Break this change. Prove each finding with a run against the real code, such as a failing test, a repro script, or command output that shows the wrong behavior. For a design or plan, run a prototype or probe the current tree. Keep repros in your own worktree. Report each finding with a title, the exact command, the output it printed, and the output the intent requires. Leave out anything you did not run. That covers style, naming, structure, and risks that need a future change to matter. No findings is a valid result.

Isolation: where the harness supports worktree isolation, each adversary works in its own git worktree, with the scope's uncommitted changes copied in so everyone attacks the same tree state. Where it does not, the main tree is off-limits: adversaries copy code elsewhere and keep every repro in a scratch directory under `/tmp`, and nothing is written back to the working tree.

## Verify

Rerun every finding's command yourself, in the same isolated environment. Keep a finding when the output shows the failure and the failure breaks the intent. Drop the rest. Merge findings that share a root cause into one entry, naming every adversary that found it. Report only; do not fix anything.

If a verification command has not returned, do not end the turn either: verify everything that returned first, then mark timed-out items as unverified and record them as a coverage gap.

## Output

- **Intent**: the restatement.
- **Findings**: for each verified finding, the command, its output, how it breaks the intent, and the adversary or adversaries that found it. Say "no verified findings" when none survive.
- **Dropped**: for each adversary, the count of dropped findings and why.
- **Coverage gaps**: unverified angles, why they are unverified, and what alternative coverage exists.

## One adversary is the default; upgrading is optional

The economy default is a single adversary. Add a second only under the escalation conditions above, and only when the harness can seat it; upgrading is never a precondition for a valid review.

- **Qoder**: if the harness cannot spawn parallel subagents, or cannot seat an adversary from a different model family, run the default single adversary — in one subagent or yourself — and use sequential independent passes (one fresh pass per angle) when you want more than one attempt. Note which angles ran in the report. The upgrade path is a second parallel, cross-family seat once the harness allows it.
- **Claude Code**: when a cross-family model is unavailable, run the second adversary on the default same-family model and say so.

Neither the economy run nor a degraded run relaxes the proof bar: unproven suspicions are dropped, not reported as findings. "No verified findings" is a valid result.
