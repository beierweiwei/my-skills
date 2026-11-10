---
name: arena
description: "Run two or more parallel candidates at the same task, pick a base, graft the strongest parts of the losers into it. Use for /arena, \"arena this\", \"throw it in the arena\", or when one attempt at a non-trivial artifact would lock in the wrong shape."
disable-model-invocation: true
---

> Provenance: forked from `backnotprop/pstack@0.15.36` (`skills/arena`; MIT, Copyright (c) 2026 Lauren Tan — see `LICENSE`).
> Changes: economy-tier defaults — two candidates (the minimum that makes an arena), cross-judge skipped by default, candidate seats on the parent model (`inherit-parent`) unless a model is named explicitly;
> the `arena runners` line in a `pstack-models` settings file is an optional enhancement, never a dependency — when the file, the line, or a seat is missing or invalid, candidates run on the parent model (omit `model`) with no error and no interruption;
> harness-neutral wording for subagents, worktrees, and model seats (no harness-specific rule paths, no hardcoded model families); principle skills inlined so the fork is self-contained. Re-check upstream on later revisions.

# Arena

Open a todolist with one entry per phase (Frame, Fan out, Cross-judge, Pick, Graft, Verify) before launching anything.

## Economy default

**Two candidates** is the default arena: the minimum that can compete. Do not fan out three or more unless the task genuinely covers multiple design directions and each extra candidate buys a direction the others do not.

**The cross-judge is off by default.** In the default run the main session scores each candidate against the rubric criterion by criterion and picks the base. The synthesis note must record `judge: skipped — economy default（评委跳过：经济档默认）`.

Escalate to one read-only judge only when both hold:

- the artifact is an architecture or public-API design, and
- the candidates diverge on direction, not merely on detail.

When a judge runs, prefer a model from a different family if the harness can seat one; otherwise run it on the parent model. An escalated arena gets one judge seat, never more.

## Phase A: Frame

Every candidate gets the same prompt, so the prompt is the contract.

1. State the artifact each candidate is producing.
2. Derive the rubric: 3-6 concrete gradeable criteria for what success looks like on *this* task. It is the picker's tool in Phase D. Candidates only see the task, not the rubric.
3. Pick the runners. Default: two candidates, each on the parent model (`inherit-parent`) — omit `model` so the seat inherits the parent. If the harness or a `pstack-models` settings file provides an `arena runners` line, treat it as an optional enhancement: use the named seats, and when the file, the line, or an entry is missing, or the subagent tool rejects an entry, run that candidate on the parent model (omit `model`) and say so. A missing or malformed settings file never blocks the arena and never raises an error. Use the same model for both seats when the work is generation-bound rather than judgment-sensitive.
4. Give each candidate its own output location (a git worktree where possible, otherwise `/tmp/arena-<slug>/candidate-<n>/`), so candidates never write to the same tree state.

## Phase B: Fan out

Spawn all candidates in one message with background execution where the harness supports it, each with the task, the path to the shared grounding, its own output path, and instructions to produce the artifact and a short rationale naming the alternatives it considered and what it rejected.

Where the harness cannot run subagents in parallel, run the candidates sequentially in their own output locations and say so in the synthesis note. If a candidate produces no output, proceed with N-1 and note the dropout.

## Phase C: Cross-judge

Skip this phase in the default run; the main session self-scores in Phase D. When the escalation condition above holds, spawn one readonly judge subagent. The judge sees the rubric and the candidates **blind, by path label** (`candidate-1`, `candidate-2`) — never by runner or model — scores each criterion, and recommends a base with rationale. It runs in parallel with your reading in Phase D. Do not spawn it while candidates are still writing. If no judge seat can run, skip the judge, pick the base alone in Phase D, and say so.

## Phase D: Pick a base

Read every candidate end to end before picking, blind to which runner produced it. Score each against the rubric criterion by criterion, not on holistic feel, and compare with the cross-judge when Phase C ran one. Agreement on the base confirms the pick. Disagreement means one of you is biased or the rubric was ambiguous; read both rationales before deciding.

Pick the base a future maintainer can extend most easily without breaking invariants. When two feel tied, prefer the cleaner boundary or smaller API that keeps the result coherent under one mental model.

## Phase E: Graft

Walk each losing candidate once more for what is worth porting into the base, usually one or two things per candidate, not most of it. Fold each graft in by hand so the result stays coherent under one mental model. Do not paste mechanically.

If the candidates converge on one shape, that is strong agreement. Note it and ship the consensus shape; no graft is needed. If they wildly diverge, Phase A was under-specified — reframe and re-run rather than averaging the divergence.

## Phase F: Verify

Verify the synthesized artifact like any other output: run it against the real code and observe the behavior; do not inspect it on faith. If verification finds a problem the arena missed, either Phase A was wrong (re-frame and re-run) or a candidate caught it and you missed the graft (go back to Phase E). Do not paper over it.

## Outputs

One synthesized artifact, with a short synthesis note alongside naming:

- the base and why;
- the judge's verdict, or `judge: skipped — economy default（评委跳过：经济档默认）`; when a judge ran, also the escalation reason;
- each graft with its source candidate;
- what was rejected and why;
- any convergence or dropouts;
- the verification result.
