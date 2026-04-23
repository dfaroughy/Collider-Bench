# Trajectory failure taxonomy (TAT)

Adapted from Terminal-Bench 2.0's Terminal Agent Taxonomy (Merrill et al.,
arXiv:2601.11868), itself a pruning of the Multi-Agent System Taxonomy
(MAST; Pan et al., 2025) to single-agent CLI settings. Nine failure modes,
three top-level classes. Used by
[`trajectory_judge.py`](trajectory_judge.py) to label each run's
`session_log.txt` + `report.md`.

## Usage notes

- Modes are **not mutually exclusive**. A single run can match multiple.
- Each mode is independently evaluated against its rubric; the judge returns
  `{matched: bool, evidence: [...]}` per mode.
- "Evidence" is 1–3 short quotes or turn pointers from the transcript — not
  paraphrase. If a mode matches without concrete evidence, the judge should
  return `matched: false`.
- The judge is instructed to be **conservative**: when in doubt, NO MATCH.
  False positives here erode the signal faster than false negatives.

---

## Execution

### `disobey_spec` — Disobey specification

**Framing.** The agent materially contradicts explicit task directives —
hard (*"must"*, *"required"*, *"shall"*, explicit prohibitions) or soft
(*"should"*, *"recommended"*). Pure format/schema violations are excluded.

**Decision procedure.**
1. Locate directives in TASK.md / AGENTS.md. If none present → NO MATCH.
2. Check for contradiction — using wrong source of truth, required method
   replaced with placeholder, required output path ignored, forbidden
   operation performed, fabricating data instead of recovering it from the
   specified source.
3. Assess materiality. Format-only shortfalls → NO MATCH. Wrong source of
   truth or missing mandated artifact → material.
4. Check for correction. If the agent fully reversed the violation before
   completion → NO MATCH.
5. If 2–3 satisfied and 4 not → MATCH.

### `step_repetition` — Step repetition

**Framing.** The agent re-executes the same phase (same sub-goal, tool,
target, and underlying method) multiple times without a meaningful
strategy change. Includes abort-loops and redundant verification runs.

**Decision procedure.**
1. Identify phases / sub-goals. If only one phase present → NO MATCH.
2. Within any phase, count semantically identical actions across distinct
   turns. Parameter tweaks that don't change the method are superficial.
3. Switching tools, changing algorithms, or introducing meaningfully
   different inputs counts as **progress**, not repetition.
4. If two or more semantically identical actions within one phase → MATCH.

### `unaware_termination` — Unaware of termination conditions

**Framing.** The agent continues acting past a reasonable stopping point
— after a clear success, after established futility, or after declaring
completion — without new justification.

**Decision procedure.**
1. Find explicit success or completion statements in the transcript.
2. Check whether the agent continues non-trivial actions afterwards.
3. If continuation has no stated new sub-goal tied to the task → MATCH.

---

## Coherence

### `reasoning_action_mismatch` — Reasoning-action mismatch

**Framing.** The agent's stated reasoning or claims (e.g., "tests passed",
"requirements satisfied") are contradicted by observable actions, logs, or
artifacts.

**Decision procedure.**
1. Find claims of success / satisfaction / passing checks in the agent's
   prose.
2. Check the corresponding action or log. If the claim is contradicted by
   visible failure (non-zero exit, failed assertion, null values in output)
   → MATCH.
3. Transient mid-debug claims that are later corrected → NO MATCH.

### `context_loss` — Context loss

**Framing.** The agent forgets or contradicts relevant recent context —
environment state (files, configs, errors) or semantic commitments (plans,
clarified goals).

**Decision procedure.**
1. Identify facts established earlier in the transcript (a file read, an
   error seen, a plan committed to).
2. Check for later actions or claims that contradict those facts.
3. If the contradiction is not explicitly justified by new information
   → MATCH.

### `task_derailment` — Task derailment

**Framing.** The agent deviates from the intended objective or focus,
spending substantive effort on unrelated or unproductive actions.

**Decision procedure.**
1. Identify the task's primary objective (from TASK.md / instruction).
2. Check whether a non-trivial span of turns (≥ ~5% of the transcript)
   pursues something orthogonal to that objective.
3. Exploratory context-gathering that serves the objective → NO MATCH.
4. If yes → MATCH.

---

## Verification

### `premature_termination` — Premature termination

**Framing.** The agent declares the task complete or presents a final
answer before satisfying the explicit objectives or delivering required
artifacts.

**Decision procedure.**
1. Identify the task's required deliverables (filled HEPRecastData/*,
   report.md, etc.).
2. Check whether each deliverable exists and is non-empty at the moment
   the agent declares completion.
3. If any required deliverable is absent, empty, or still null-valued
   when the agent terminates → MATCH.

### `no_incorrect_verification` — No or incorrect verification

**Framing.** The agent marks the task completed or bypasses a designated
verifier without performing a substantive check of the actual deliverable
(or ignores failing core checks).

**Decision procedure.**
1. Check whether the agent ran a verification step before declaring done
   (pytest, bin/run-analysis, sanity-plot inspection, comparison to paper
   figures, etc.).
2. If no verification ran → MATCH.
3. If verification ran and **failed** but the agent still declared done
   → MATCH.

### `weak_verification` — Weak verification

**Framing.** The agent relies on verification that fails to cover
task-critical properties — e.g., checking file existence but not
contents, or fabricating data to satisfy a check.

**Decision procedure.**
1. Identify what verification was actually performed.
2. Check whether it covers the task's critical properties (e.g., for
   recast: bin-by-bin values within tolerance of the reference, not just
   "the YAML parses").
3. If the check is present but superficial relative to the task's stakes
   → MATCH.
