---
name: interrogate
description: "Use for \"interrogate\", \"adversarial review\", \"multi-model review\", \"challenge this\", \"stress test this code\", \"find blind spots\", or \"tear this apart\". Adversaries try to break a change or design, and only findings proven by a run survive."
disable-model-invocation: true
---

> Provenance: forked from `backnotprop/pstack@0.15.36` (MIT).
> Changes: made self-contained — scope framing, adversary fan-out, and verification are inlined here instead of delegating to separately installed skills;
> adds an explicit degradation path (single adversary when a harness cannot run parallel subagents or cross-family models) framed as an upgrade path, not a precondition;
> harness wording generalized to a subagent/worktree model. Re-check upstream on later revisions.

# Interrogate

Adversaries try to break a change or a design. A finding survives only when a run proves it. Report the findings. Do not apply fixes.

## Frame

The scope is what the user names. Otherwise it is the branch diff from its merge base with the base branch, through the working tree. Restate the intent in one paragraph from the user's message, the commits, and the PR description, and state it back before fanning out: what problem the change claims to solve, what behavior it must preserve, and which trade-offs it accepts. Ask the user when the intent is unclear; never attack a guessed intent.

## Fan out

Default to 3–4 adversaries, one subagent each, spawned in a single message in the background so they run in parallel. Give each a distinct attack angle — for example correctness, edge cases and failure modes, data and state, security and performance — so they do not all fail the same way. Each adversary gets the scope, the intent, and this brief:

> Break this change. Prove each finding with a run against the real code, such as a failing test, a repro script, or command output that shows the wrong behavior. For a design or plan, run a prototype or probe the current tree. Keep repros in your own worktree. Report each finding with a title, the exact command, the output it printed, and the output the intent requires. Leave out anything you did not run. That covers style, naming, structure, and risks that need a future change to matter. No findings is a valid result.

Isolation: where the harness supports worktree isolation, each adversary works in its own git worktree, with the scope's uncommitted changes copied in so everyone attacks the same tree state. Where it does not, the main tree is off-limits: adversaries copy code elsewhere and keep every repro in a scratch directory under `/tmp`, and nothing is written back to the working tree.

## Verify

Rerun every finding's command yourself, in the same isolated environment. Keep a finding when the output shows the failure and the failure breaks the intent. Drop the rest. Merge findings that share a root cause into one entry, naming every adversary that found it. Report only; do not fix anything.

## Output

- **Intent**: the restatement.
- **Findings**: for each verified finding, the command, its output, how it breaks the intent, and the adversary or adversaries that found it. Say "no verified findings" when none survive.
- **Dropped**: for each adversary, the count of dropped findings and why.

## Degradation: fewer adversaries, same proof bar

Parallel, cross-family adversaries are an enhancement, not a precondition.

- **Qoder**: if the harness cannot spawn parallel subagents, or cannot seat an adversary from a different model family, degrade to a single adversary — run the brief yourself or in one subagent, and use sequential independent passes (one fresh pass per angle) when you want more than one attempt. Note the degradation and which angles ran in the report. The upgrade path is adding parallel, cross-family adversaries once the harness allows it.
- **Claude Code**: when a cross-family model is unavailable (for example a missing grok seat), run the adversary on the default same-family model and say so.

A degraded run never relaxes the proof bar: unproven suspicions are dropped, not reported as findings.
