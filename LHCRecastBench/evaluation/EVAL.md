# Evaluation

Post-hoc evaluation of agent recast runs. Four layers, each probing a different aspect. None are visible to the agent during its run — `LHCRecastBench/evaluation/` is `--tmpfs`'d inside the sandbox.

All four write their outputs to `<run_dir>/eval/` (sibling of `workspace/`, not inside it).

## Inputs

Every evaluator reads from the same workspace layout:

```
<run_dir>/workspace/
  results/<histogram>.yaml     # agent's filled histogram (metadata block on top,
                               # HEPData-style histogram below — two YAML docs)
  datasets.yaml
  analysis.py | analysis/*.py
  report.md
  session_log.txt              # CLI stream-json (used by LLM judges)
```

The reference file lives at `LHCRecastBench/tasks/shared/<paper>/reference/<file>.yaml`. The scorer reads `task_id` from `<run_dir>/run_info.json`, picks `paper` from `task.toml`, finds `data_filename` from the single histogram file under `tasks/<task_id>/template/`, and reads `header_name` from `dependent_variables[0].header.name` of that file. The matching series in the reference is then compared.

Tasks may set `[metrics].score = "shape"` in `task.toml`. For these shape-only tasks, `score.py` still reports normalization diagnostics, but `overall_combined` is the shape score alone.

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
- top-level `overall_shape`, `overall_normalization`, `overall_combined` (`overall_combined = overall_shape` for shape-only tasks)
- `n_filled`, `n_bins` — sanity flag for "did the agent fill anything?"

**Why BC, not Pearson?** BC reduces to Pearson χ² at high counts but handles low/zero-count bins correctly (`0·ln(0) ≡ 0`, no divide-by-zero, still asymptotically χ²-distributed). In HEP histograms with mixed high-yield peaks and near-zero tails it's the safer default.

**Caveat on the Poisson assumption.** Our "observations" are weighted yields (σ×L×ε), not integer event counts. BC's log-likelihood is derived for Poisson data; using it on weighted yields is the standard HEP approximation and fine for ranking. A more rigorous variant would transform yields to effective counts `N_eff = yield² / σ²_yield` using published errors.

```bash
python -m LHCRecastBench.evaluation.score <run_dir>
python -m LHCRecastBench.evaluation.score <run_dir_a> <run_dir_b>   # compare
```

### 2. `plot_recast.py` — histogram comparisons (visual)

Produces per-table PNGs under `eval/plots/`. For each reference YAML it emits two figures:

- `<stem>_yield.png` — absolute event yields using the task's `[metrics].plot` setting: `Events/bin` shows raw bin contents matching the submitted `results/*.yaml` values and the scorer; `Events/GeV` shows bin-width-divided densities for visual comparison across variable-width bins
- `<stem>_shape.png` — unit-area normalised (same, per unit area)

Each figure has a top panel with CMS as a filled histogram, Recast as a solid step histogram, and a hatched CMS uncertainty band. The band combines the task's `[metrics].tolerance` value used in the p-value calculation with Poisson counting uncertainty in quadrature. The ratio sub-panel plots `recast / CMS` as a histogram with the matching relative uncertainty band and a horizontal line at 1. Uses `mplhep.style.CMS`; no titles, legend inside the top panel. Series with both arrays all-zero are skipped.

```bash
python -m LHCRecastBench.evaluation.plot_recast <run_dir>
```

### 3. `llm_judge.py` — provenance audit + trajectory narrative

An LLM (Opus by default) reads the full session log, filled `results/*.yaml`, hidden evaluator-only reference, and artifacts. Emits `eval/judge_scores.json`, `eval/judge_trajectory.md`, and — when corrections are needed — `eval/results_corrected_by_judge/` plus `eval/score_corrected.json`.

The reference is not visible to the agent during the task. The judge must not penalize an agent for failing to compare against truth; `score.py` handles numeric agreement. The judge uses the hidden reference only to detect suspicious copying/leakage and to understand broad result scale.

The judge has two responsibilities:

1. **Provenance audit**: check that values in `results/*.yaml` reflect the agent's actual work.
2. **Trajectory narrative**: describe how the agent reasoned, adapted, got stuck, used tools, and reported limitations.

**Provenance audit** classifies every dependent-variable series:

| Class | Meaning |
|---|---|
| `TRACEABLE` | Values trace to real tool output, code output, extraction, or documented calculation |
| `PARTIALLY_TRACEABLE` | Some values trace to work; others are missing or unsupported |
| `NULL_BUT_COMPUTED` | `results/*.yaml` left null, but the agent did compute values elsewhere (e.g. `results.json`, printed to session) — the agent forgot to fill the template |
| `UNTRACEABLE` | Values appear in `results/*.yaml`, but no source is found |
| `FABRICATED` | Values contradict the apparent computation or were invented |
| `COPIED_OR_LEAKED` | Values match hidden reference data without visible independent computation |

If the judge can recover the agent's actual computed values for problematic/null series, it writes them into `results_corrected_by_judge/` and `score.py` is automatically re-run on that corrected directory (output: `score_corrected.json`). Corrected values must come from the agent's own work, never from the hidden reference.

**Trajectory narrative** — gives qualitative insight into planning, tool use, scientific judgment, creative workarounds, avoidable stuck points, and honesty about limitations. It is not a rigid scorecard. Rubric in [judge_rubric.md](judge_rubric.md).

```bash
python -m LHCRecastBench.evaluation.llm_judge <run_dir>
```

## Output layout

After all four have run, `eval/` contains:

```
eval/
  score.json                           # Baker-Cousins shape/norm/total + KS p-values
  summary.md                           # human-readable digest; shows Submitted/Audited scores when judged
  plots/                               # CMS/recast histogram PNGs with tolerance bands (yield + shape)
    <histogram>_yield.png
    <histogram>_shape.png
  judge_scores.json                    # (opt-in) provenance audit + trajectory JSON
  judge_trajectory.md                  # (opt-in) rendered trajectory narrative
  results_corrected_by_judge/          # only if judge applied corrections
  score_corrected.json                 # score.py re-run on corrected data when overrule/action requests it
```

## Key design decisions

- **One histogram, one series per task.** Each task scores exactly one filled histogram against one reference series — no multi-table or multi-signal aggregation in the scorer. Cross-task / cross-runner aggregation happens at a higher level if you want it.
- **Shape is the BC likelihood ratio, not Pearson χ².** BC reduces to Pearson at high counts but is well-defined at zero-expectation bins and is asymptotically χ²-distributed at lower counts. Per-bin uncertainty enters via the Poisson likelihood itself.
- **Bounded score is a z-score, not a raw p-value.** `score = exp(−√λ / 5)`. The p-value is reported alongside for the statistical reading; the bounded score gives a usable gradient when p-values saturate at zero.
- **`λ_total = λ_shape + λ_norm` is an algebraic identity, not an asymptotic approximation.** Falls out cleanly from profiling α = ΣO/ΣE over the Poisson log-likelihood.
- **No per-bin pass/fail.** The scorer reports the Baker-Cousins triple (shape, norm, total) and KS — that's the full picture. Per-bin "passing" thresholds were dropped because they obscured the shape-vs-norm decomposition that's actually informative.
- **Multiple evaluators because no single number tells the truth.** Shape can look fine while norm is off by 50× (or vice versa); the LLM judge's provenance audit catches copying, fabrication, and unjustified post-hoc adjustments. `summary.md` reports both Submitted and Audited scores when the LLM judge has run.

## Running on a completed run

`score` + `plot_recast` + `render_eval` run automatically at the end of every
agent launch (`scripts/run-agent`). To re-run them on an existing run dir
(e.g. after a reference update), or to add the opt-in LLM judges:

```bash
./scripts/launch_eval.sh runs/<runner>_<model>/<task-id>_<hex>/
./scripts/launch_eval.sh runs/.../  --judge              # add provenance audit + trajectory narrative
```

All evaluators read task identity from `<run_dir>/run_info.json` + the
task's `task.toml` — nothing needs to be passed on the CLI.
