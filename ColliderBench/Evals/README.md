# Evals

Post-hoc evaluation of agent recast runs. Two pieces:

- **`score`** — compute histogram-comparison metrics, write `eval/score.json` and `eval/plots/*.png`.
  `score.json` is the only metric artifact; no derived Markdown digest is produced.
- **`judge`** — opt-in LLM provenance audit + trajectory narrative (uses `claude` CLI).

## Layout

```
Evals/
  histograms.py      # HEPData YAML → Histogram, Series, Aligned numpy arrays
  metrics/
    baker_cousins.py     # toy-calibrated p-value (kind = "shape" or "normalization")
    bin_error.py         # mean_abs_frac_error_pct + total_frac_error_pct
    jsd.py               # Jensen-Shannon divergence + distance (base-2 log)
  plotting.py        # CMS-style yield + shape comparison PNGs
  score.py           # orchestrator (CLI: python -m ColliderBench.Evals.score)
  judge.py           # LLM provenance audit
  judge_rubric.md    # rubric for the judge
```

No registries, no class hierarchies: each metric is a plain function taking
aligned numpy arrays and returning a scalar (or small dict). `score.py`
calls them directly and assembles the JSON.

## `score.json` schema

```jsonc
{
  "task_id": "sus-16-046_shape-T5Wg",
  "paper": "CMS-SUS-16-046",
  "header_name": "STGAMMA",
  "n_bins": 12,
  "n_filled": 12,
  "score_mode": "shape_norm",       // from task.toml [metrics].mode

  "shape": {
    "mean_abs_frac_error_pct":  5.2,    // post-normalization, bin-by-bin relative
    "p_value":                  0.32,   // Baker-Cousins λ_shape, toy-calibrated
    "jensen_shannon":           0.04,   // JSD, base-2 log, in [0, 1]
    "jensen_shannon_dist":      0.20,   // sqrt(JSD), the true metric
    "d_bar":                    0.042,  // ½·L1(p,q) = total-variation distance, in [0,1]
    "d_max":                    0.052,  // max_i |p_i − q_i|, in [0,1] (worst-bin)
    "dmax_dbar_ratio":          1.000   // d_max / d_bar, in [1/K, 1]; 1 = single-bin failure
  },

  "normalization": {
    "mean_abs_frac_error_pct":  1.8,    // |Σobs − Σref| / Σref × 100
    "Delta":                    0.018,  // = mean_abs_frac_error_pct / 100  (paper-text Δ)
    "rmsle":                    0.42    // sqrt((1/K) Σ [ln(N_obs+1) − ln(N_ref+1)]²)
  }
}
```

Any metric value can be `null` when undefined (zero total yield, mismatched
sizes, no positive truth bins). Consumers must handle nulls.

`score_mode` (from `task.toml [metrics].mode`) controls which blocks appear:

| `score_mode` | `shape` block | `normalization` block | `<stem>_shape.png` | `<stem>_yield.png` |
|---|---|---|---|---|
| `shape`      | yes | **omitted** | rendered | **skipped** |
| `yield`      | **omitted** | yes | **skipped** | rendered |
| `shape_norm` | yes | yes | rendered | rendered |

The omitted JSON block is genuinely absent — consumers should test
`score.get("normalization") is not None`, not check for null fields inside.
Plot files are similarly absent on disk for skipped variants.

## Metrics

### Baker-Cousins p-value (`shape`)

```
λ_shape = 2 Σ O · ln(O / Ê),  Ê = α·E,  α = ΣO/ΣE
```

Calibrated by 1M Poisson + log-normal toys under the null. Reported value is
`Pr(λ_toy ≥ λ_obs)`. Lower = more shape disagreement. The normalization
analogue (`λ_norm`) isn't currently in `score.json` — only the bin-wise
fractional error of totals lives in the `normalization` block. Add a
`normalization.p_value` field if you need it.

### Jensen-Shannon (`shape`)

JSD on the unit-area-normalized distributions, base-2 log. Range `[0, 1]`,
0 = identical, 1 = orthogonal supports. We also report `√JSD` as
`jensen_shannon_dist` — same range, but a true metric (satisfies the
triangle inequality).

### Mean absolute fractional error

- **`shape.mean_abs_frac_error_pct`** = `100 · mean_i (|p_i - q_i| / p_i)` over
  reference-positive bins, with both histograms first normalized to unit area.
  Pure shape metric. Per-bin **relative** error — small bins dominate.
- **`shape.d_bar`** = `(1/2) · Σ_i |p_i - q_i|` on unit-area-normalized
  histograms — the total-variation distance between p and q, in `[0, 1]`.
  Per-bin denominator is the constant `1/K` (mean target bin) rather than each
  bin's own value, so large bins contribute proportionally — complement to
  `mean_abs_frac_error_pct`'s small-bin weighting.
- **`shape.d_max`** = `max_i |p_i - q_i|` on unit-area-normalized histograms,
  in `[0, 1]`. Worst single-bin probability difference: catches catastrophic
  single-bin failures that integrated metrics average away. K-independent
  (no `K·` factor) so values are comparable across tasks with different bin
  counts. Only metric in the schema that's *not* a sum/mean.
- **`shape.dmax_dbar_ratio`** = `d_max / d_bar`, bounded in `[1/K, 1]`.
  Concentration of the shape disagreement: `1` means the entire L1 mass is
  in one bin (a single-bin failure); `1/K` means the disagreement is
  spread uniformly across all K bins. Disambiguates "concentrated outlier"
  from "diffuse mismatch" runs that have the same `d_bar`. `null` when
  `d_bar == 0` (perfect match).
- **`normalization.mean_abs_frac_error_pct`** = `100 · |Σobs - Σref| / Σref`.
  Single number, naming kept symmetric with the shape block.
- **`normalization.Delta`** = `|Σobs - Σref| / Σref` (raw fraction, no ×100).
  Matches the Δ_norm notation in the paper: the natural scoring primitive
  for `sim` (`shape_norm`) tasks where the absolute event rate is the
  thing being predicted. Mathematically the same as
  `mean_abs_frac_error_pct / 100`; both fields coexist for convenience.
- **`normalization.rmsle`** = `sqrt((1/K) Σ_i [ln(N_obs_i + 1) − ln(N_ref_i + 1)]²)`.
  Root-mean-squared log error on raw bin yields. Designed for non-negative
  values that span orders of magnitude — typical of HEP histograms (signal
  peaks at ~10⁴ events alongside tail bins at ~10). Unlike linear MSE,
  every bin contributes on equal log-footing, so a 100× tail-bin error
  registers as strongly as a 100× peak-bin error. The `+1` offset handles
  zero-count bins. Unbounded above; lower = better.

## CLI

```bash
# Score one or more runs (writes <run>/eval/score.json + plots).
python -m ColliderBench.Evals.score runs/<runner>_<model>/<task>_<id>/

# Skip plots (faster).
python -m ColliderBench.Evals.score --no-plots runs/.../

# Override toy count for quick iteration (default 1,000,000).
python -m ColliderBench.Evals.score --n-toys 10000 runs/.../

# Opt-in: LLM provenance audit (uses the claude CLI).
python -m ColliderBench.Evals.judge runs/.../
```

## Inputs

Every CLI reads from the run directory:
```
<run_dir>/
  run_info.json                            # task_id, runner, model, usage
  workspace/
    results/<file>.yaml                    # agent's filled histogram
    session.jsonl                          # vendor-native event stream (judge only)
    report.md, datasets.yaml, results.json # judge artifacts (optional)
```

The reference (truth) lives outside the run dir at
`ColliderBench/tasks/shared/<paper>/reference/<file>.yaml` and is hidden
from the agent during the run.

## What's gone vs. the old `evaluation/` folder

- No `_resolve.py` / `RunPaths` dataclass — `score.resolve_paths(run_path)`
  returns a dict with all the paths the eval code needs.
- No `context.py` / `EvalContext` / `MetricRunner` registry — metrics are
  plain functions called directly by `score.py`.
- No dual-schema `score.json` (both `series.*` and `metrics.*`) — the new
  schema is metric-name-keyed and that's the only one written.
- No `final_score.combined` denormalization into `run_info.json` — read
  `eval/score.json` if you want a run's score.
- Tasks no longer declare `[metrics].report` — both metrics always run.
