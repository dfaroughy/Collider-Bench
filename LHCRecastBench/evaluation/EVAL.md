# Evaluation

Post-hoc evaluation of agent recast runs. Four layers, each probing a different aspect. None are visible to the agent during its run — `LHCRecastBench/evaluation/` is `--tmpfs`'d inside the sandbox.

All four write their outputs to `<run_dir>/eval/` (sibling of `workspace/`, not inside it).

## Inputs

Every evaluator reads from the same workspace layout:

```
<run_dir>/workspace/
  results/<histogram>.yml      # agent's filled histogram
  results/description.toml     # per-histogram metadata
  datasets.yaml
  analysis.py | analysis/*.py
  report.md
  session_log.txt              # CLI stream-json (used by LLM judges)
```

The reference histogram lives at `LHCRecastBench/tasks/shared/<paper>/histograms/<histogram>.yaml`. The scorer reads `task_id` from `<run_dir>/run_info.json`, picks `paper` + `data_filename` + `header_name` from `task.toml` + `description.toml`, and compares the matching series.

## The evaluators

### 1. `score.py` — Baker-Cousins shape/norm/total + KS (automatic)

Runs at the end of every agent launch; emits `eval/score.json`. One histogram, one series, one pass.

**Baker-Cousins likelihood-ratio decomposition.** On the index-aligned (both-non-null) subset:

```
λ_total  = 2·Σ [ O·ln(O/E) − (O − E) ]        ~ χ²(N)     goodness of fit
λ_shape  = 2·Σ O·ln(O/Ê)     where Ê = α·E    ~ χ²(N−1)   shape only (α = ΣO/ΣE)
λ_norm   = 2·[ ΣO·ln(ΣO/ΣE) − (ΣO − ΣE) ]     ~ χ²(1)     total only
```

`λ_total = λ_shape + λ_norm` is an algebraic identity (profile + constraint), not just asymptotic.

Each λ yields a p-value via `scipy.stats.chi2.sf(λ, dof)` and an effective sigma `z = √λ`. The **bounded score** is the monotone `exp(−z / 5)` — gentler than the raw p-value (which saturates at zero for modest deviations on high-stat samples), still a calibrated statistical quantity. At `z=5` → 0.37; `z=10` → 0.14.

**Fields in `score.json`:**

- `series.shape`: `{lambda, dof, lambda_per_dof, z, p_value, score}`
- `series.normalization`: `{lambda, dof=1, z, p_value, score, ratio, log10_ratio}` — ratio kept for human readability (physicists read "2× off" more naturally than "z=8.3")
- `series.total`: `{bc_stat, dof, z, p_value}`
- `series.ks`: `{stat, p_value, n_eff}` — secondary unit-area shape diagnostic
- `series.combined` = √(shape.score · norm.score)
- `series.diagnosis`: `GOOD` / `SHAPE OK, NORM BAD` / `SHAPE BAD, NORM OK` / `BOTH BAD`
- top-level `overall_shape`, `overall_normalization`, `overall_combined` (mirrors of `series.*.score`)
- `n_filled`, `n_bins` — sanity flag for "did the agent fill anything?"

**Why BC, not Pearson?** BC reduces to Pearson χ² at high counts but handles low/zero-count bins correctly (`0·ln(0) ≡ 0`, no divide-by-zero, still asymptotically χ²-distributed). In HEP histograms with mixed high-yield peaks and near-zero tails it's the safer default.

**Caveat on the Poisson assumption.** Our "observations" are weighted yields (σ×L×ε), not integer event counts. BC's log-likelihood is derived for Poisson data; using it on weighted yields is the standard HEP approximation and fine for ranking. A more rigorous variant would transform yields to effective counts `N_eff = yield² / σ²_yield` using published errors.

```bash
python -m LHCRecastBench.evaluation.score <run_dir>
python -m LHCRecastBench.evaluation.score <run_dir_a> <run_dir_b>   # compare
```

### 2. `plot_recast.py` — histogram comparisons (visual)

Produces per-table PNGs under `eval/plots/`. For each reference YAML it emits two figures:

- `<stem>_yield.png` — absolute event yields (`Events / <unit>`), bin-width-divided so variable binning is honest
- `<stem>_shape.png` — unit-area normalised (same, per unit area)

Each figure has a top panel with step histograms (CMS solid, Recast dashed, one color per series) and a ratio sub-panel plotting `recast / CMS` per bin with a horizontal line at 1. Error bars come from the reference `symerror`. Uses `mplhep.style.CMS`; no titles, legend inside the top panel. Series with both arrays all-zero are skipped.

```bash
python -m LHCRecastBench.evaluation.plot_recast <run_dir>
```

### 3. `llm_judge.py` — reasoning quality + provenance verification

An LLM (Opus by default) reads the full session log, filled HEPRecastData, reference, and artifacts. Emits `eval/judge_scores.json`, `eval/judge_failure_report.md`, and — when corrections are needed — `eval/HEPRecastData_corrected_by_judge/` plus `eval/score_corrected.json`.

**Six scoring dimensions (1–5 each):** Diagnosis Quality, Creative Problem-Solving, Scientific Honesty, Hallucination, Tool Use Efficiency, Audit Trail Completeness.

**Provenance verification** classifies every dependent-variable series:

| Class | Meaning |
|---|---|
| `GENUINE` | Values trace to real tool output or computed result in the session |
| `COPIED` | Values identical to the published reference with no independent computation — cheating |
| `PARTIALLY_GENUINE` | Some bins real, others copied/fabricated |
| `NULL_BUT_COMPUTED` | HEPRecastData left null, but the agent did compute values elsewhere (e.g. `results.json`, printed to session) — the agent forgot to fill the template |

If the judge finds `COPIED` or `NULL_BUT_COMPUTED` series, it **writes the corrected values** into `HEPRecastData_corrected_by_judge/` and `score.py` is automatically re-run on that corrected directory (output: `score_corrected.json`). This is how the benchmark stays robust to both "100% by copying" and "0% by forgetting to fill".

**Failure report** — catalogs every reasoning failure with a type label, severity, evidence, and whether the agent self-corrected. Extensible taxonomy: `POLLING_VIOLATION`, `NORMALIZATION_ERROR`, `HALLUCINATION`, `PREMATURE_SURRENDER`, `MISSED_WORKAROUND`, `FORMAT_BLINDNESS`, `SPECIFICATION_MISREAD`, `TOOL_MISUSE`, `BIAS_PROPAGATION`, `OVERCLAIMING`, `INCOMPLETE_SEARCH`, plus new types the judge is free to coin. Rubric in [judge_rubric.md](judge_rubric.md).

```bash
python -m LHCRecastBench.evaluation.llm_judge <run_dir>
```

## Output layout

After all four have run, `eval/` contains:

```
eval/
  score.json                           # Baker-Cousins shape/norm/total + KS p-values
  summary.md                           # human-readable digest of the above
  plots/                               # step-histogram PNGs (CMS vs recast, yield + shape)
    <histogram>_yield.png
    <histogram>_shape.png
  judge_scores.json                    # (opt-in) 6-dim reasoning + provenance
  judge_failure_report.md              # (opt-in) typed failure catalog
  trajectory_judge.json                # (opt-in) 9-mode TAT taxonomy
  results_corrected_by_judge/          # only if judge applied corrections
  score_corrected.json                 # score.py re-run on the corrected data
```

## Key design decisions

- **One histogram, one series per task.** Each task scores exactly one filled histogram against one reference series — no multi-table or multi-signal aggregation in the scorer. Cross-task / cross-runner aggregation happens at a higher level if you want it.
- **Shape is the BC likelihood ratio, not Pearson χ².** BC reduces to Pearson at high counts but is well-defined at zero-expectation bins and is asymptotically χ²-distributed at lower counts. Per-bin uncertainty enters via the Poisson likelihood itself.
- **Bounded score is a z-score, not a raw p-value.** `score = exp(−√λ / 5)`. The p-value is reported alongside for the statistical reading; the bounded score gives a usable gradient when p-values saturate at zero.
- **`λ_total = λ_shape + λ_norm` is an algebraic identity, not an asymptotic approximation.** Falls out cleanly from profiling α = ΣO/ΣE over the Poisson log-likelihood.
- **No per-bin pass/fail.** The scorer reports the Baker-Cousins triple (shape, norm, total) and KS — that's the full picture. Per-bin "passing" thresholds were dropped because they obscured the shape-vs-norm decomposition that's actually informative.
- **Multiple evaluators because no single number tells the truth.** Shape can look fine while norm is off by 50× (or vice versa); the LLM judge's provenance check catches copying. `score_corrected` is the accuracy number that survives cheating.

## Running on a completed run

`score` + `plot_recast` + `render_eval` run automatically at the end of every
agent launch (`scripts/run-agent`). To re-run them on an existing run dir
(e.g. after a reference update), or to add the opt-in LLM judges:

```bash
./scripts/launch_eval.sh runs/<runner>_<model>/<task-id>_<hex>/
./scripts/launch_eval.sh runs/.../  --judge trajectory   # add 9-mode TAT
./scripts/launch_eval.sh runs/.../  --judge llm          # add provenance + 6-dim judge
./scripts/launch_eval.sh runs/.../  --judge both
```

All evaluators read task identity from `<run_dir>/run_info.json` + the
task's `task.toml` — nothing needs to be passed on the CLI.
