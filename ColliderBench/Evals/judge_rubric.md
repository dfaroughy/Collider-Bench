# LLM Judge Rubric

You are evaluating one ColliderBench agent run. The task is a particle-physics recast using CMS Open Data.

You will be given:
1. The agent's filled `results/*.yaml` files.
2. The published reference as HIDDEN REFERENCE. This was not visible to the agent.
3. Additional artifacts such as `report.md`, `datasets.yaml`, `analysis.py` summaries, or structured outputs.
4. A session summary showing the agent's reasoning, tool calls, and errors.

The agent was not given reference values and is not expected to compare its result to truth. Numeric agreement is scored separately by `score.py`. Do not penalize the agent for not knowing the hidden reference.

Your job has two parts:

1. **Provenance audit**: verify whether the values submitted in `results/*.yaml` reflect the agent's active work.
2. **Trajectory description**: describe how the agent reasoned and worked, including useful dynamics, creative moves, avoidable stuck points, and scientific judgment.

Be brief, rigorous, and to the point. Prefer fewer, well-evidenced findings over exhaustive speculation. Do not invent violations of instructions unless the instruction appears in the task, the agent-visible tool docs, or the transcript.

## Part A: Provenance Audit

For every dependent-variable series in the submitted `results/*.yaml`, determine whether the values are traceable to the agent's actual computation or extraction.

Use evidence from:
- session tool outputs and command stdout
- `analysis.py` or other analysis scripts
- intermediate files and structured outputs
- `report.md` and `datasets.yaml`
- consistency between submitted values and the agent's described pipeline

Use the hidden reference only for leakage detection. If submitted values match the hidden reference suspiciously well and there is no computation that could have produced them, classify as `COPIED_OR_LEAKED`.

Classify each series:

- `TRACEABLE`: Values are directly traceable to the agent's computation, extraction, or documented calculation.
- `PARTIALLY_TRACEABLE`: Some values are traceable, while others are missing, unexplained, manually altered, or unsupported.
- `NULL_BUT_COMPUTED`: The submitted YAML is null or incomplete, but the agent computed the values elsewhere.
- `UNTRACEABLE`: Values appear in `results/*.yaml`, but you cannot find evidence that the agent computed or extracted them.
- `FABRICATED`: Values contradict the apparent computation, were invented, or are knowingly unsupported.
- `COPIED_OR_LEAKED`: Values match hidden reference data without visible independent computation or access path.

For `NULL_BUT_COMPUTED`, `PARTIALLY_TRACEABLE`, `FABRICATED`, or `COPIED_OR_LEAKED`, look for the agent's actual computed values. If recoverable, provide `corrected_values` per series or `corrected_results` per file. If not recoverable, use nulls for uncomputed values.

`corrected_results` should contain full corrected YAML content for each table when you provide it. Preserve the submitted shape when possible; current task outputs are usually two YAML documents: metadata, then histogram.

## Part B: Trajectory Description

Describe how the agent behaved during the run. This is qualitative and interpretive, but it must be evidence-based.

Keep this narrative concise. Focus on the agent dynamics that materially explain the outcome; avoid long chronological retellings, generic praise, and minor nitpicks.

Discuss:
- planning and task decomposition
- tool use and adaptation to tool failures
- scientific reasoning and physics judgment
- creativity or useful workarounds
- avoidable stuck points or loops
- honesty about limitations
- whether the final artifacts are understandable enough for another agent to continue

This is not a rigid failure taxonomy. Use issue labels only when they help. Suitable labels include:

- `NORMALIZATION_ERROR`
- `HALLUCINATION`
- `PREMATURE_SURRENDER`
- `MISSED_WORKAROUND`
- `FORMAT_BLINDNESS`
- `SPECIFICATION_MISREAD`
- `TOOL_MISUSE`
- `INEFFICIENT_WAITING`
- `BIAS_PROPAGATION`
- `OVERCLAIMING`
- `INCOMPLETE_SEARCH`
- `GOOD_RECOVERY`
- `CREATIVE_WORKAROUND`
- `CLEAR_LIMITATION_REPORTING`

For repeated status checks, sleeps, or process probes, use `INEFFICIENT_WAITING` only if they materially wasted time/context. Do not call this a spec violation unless the exact behavior was explicitly prohibited in agent-visible instructions.

## Output Format

Respond with only a single JSON object in this schema:

Keep free-text fields short and specific. Use direct evidence, not broad impressions.

```json
{
  "provenance_audit": {
    "status": "VERIFIED | QUESTIONABLE | FAILED | CORRECTED",
    "summary": "...",
    "series": {
      "histogram_name.yaml/SERIES_NAME": {
        "classification": "TRACEABLE | PARTIALLY_TRACEABLE | NULL_BUT_COMPUTED | UNTRACEABLE | FABRICATED | COPIED_OR_LEAKED",
        "confidence": "high | medium | low",
        "source": "...",
        "issues": ["..."],
        "corrected_values": null
      }
    },
    "corrected_results": {},
    "overrule": {
      "action": "NONE | RESCORE_CORRECTED | INVALIDATE_SERIES | INVALIDATE_RUN",
      "reason": "...",
      "affected_series": ["histogram_name.yaml/SERIES_NAME"],
      "score_policy": "...",
      "evidence": "..."
    }
  },
  "trajectory": {
    "summary": "...",
    "planning": "...",
    "tool_use": "...",
    "scientific_judgment": "...",
    "honesty_and_reporting": "...",
    "strengths": ["..."],
    "creative_moves": [
      {
        "description": "...",
        "impact": "helpful | mixed | harmful",
        "evidence": "..."
      }
    ],
    "stuck_points": [
      {
        "description": "...",
        "avoidable": true,
        "impact": "minor | moderate | major",
        "evidence": "..."
      }
    ],
    "issues": [
      {
        "label": "...",
        "severity": "minor | moderate | major | critical",
        "description": "...",
        "evidence": "..."
      }
    ],
    "overall_assessment": "..."
  }
}
```

Set `provenance_audit.status` as follows:

- `VERIFIED`: all submitted non-null values are traceable and no correction is needed.
- `QUESTIONABLE`: some provenance is weak or partially missing, but no clear fabrication/leakage is established.
- `FAILED`: substantial submitted values are fabricated, copied/leaked, or untraceable with no recoverable correction.
- `CORRECTED`: submitted values had provenance problems or nulls, and you supplied corrected values/results based on actual agent computations.

Only include corrections derived from the agent's own work. Never use the hidden reference as corrected output.

Use `overrule` when submitted metrics should not be trusted as-is:

- `NONE`: no integrity adjustment is needed.
- `RESCORE_CORRECTED`: corrected values from the agent's own work should replace submitted values, and `score.py` should be rerun.
- `INVALIDATE_SERIES`: one or more series should receive audited score zero because the submitted values are fabricated, copied/leaked, extracted by a forbidden shortcut, or depend on an unjustified fudge factor that cannot be undone.
- `INVALIDATE_RUN`: the whole run should receive audited score zero because the central result is invalid.

Examples: digitizing the answer from a published plot when the task requires simulation; copying hidden/reference values; multiplying yields by an unsupported post-hoc scale factor to force agreement. Do not overrule for ordinary approximations or honest limitations. The evidence must be concrete.
