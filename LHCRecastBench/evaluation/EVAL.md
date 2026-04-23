# Evaluation

Post-hoc evaluation of agent recast runs. Four layers, each probing a different aspect. None are visible to the agent during its run — `LHCRecastBench/evaluation/` is `--tmpfs`'d inside the sandbox.

All four write their outputs to `<run_dir>/eval/` (sibling of `workspace/`, not inside it).

## Inputs

Every evaluator reads from the same workspace layout:

```
<recast_run>/workspace/
  HEPRecastData/*.yaml       # agent's filled templates
  datasets.yaml              # samples used
  analysis.py | analysis/*.py
  report.md
  session_log.txt            # Claude stream-json (used by LLM judge + rubric)
```

Reference values live in `LHCRecastBench/papers/<arxiv>/tasks/<task>/reference/HEPRecastData/` — task-specific so `validate` / `simulate` / `recast` each compare against their own subset. Evaluators match by filename (`submission.yaml` and `description.yaml` are always skipped).

## The four evaluators

### 1. `score.py` — per-bin accuracy **and** shape/normalization (automatic)

Runs at the end of every agent launch; emits `eval/score.json`. Single pass over the YAMLs computes every numerical metric — per-bin pulls plus shape/normalization decomposition — in one JSON.

**Per-bin metrics.** For each dependent-variable series, loops over bins and computes a **pull**:

```
pull = (recast - reference) / total_err
```

- `total_err` is the stated uncertainty from the reference (sum of all `symerror`/`asymerror` entries in quadrature). If no uncertainty is published for that bin, falls back to `sqrt(|reference|)` (Poisson approximation, without any integer floor — reference values may be fractional expected yields).
- Zero-expectation bins (`reference == 0`) are scored specially: recast ≈ 0 → PASS; non-zero recast → FAIL (false positive).
- A bin **passes** iff `|pull| < 2` OR `rel_diff < 50%`. The OR is deliberate: small ratios pass under tight published errors, gross outliers fail via pull under loose errors.

Top-line: `n_pass / n_filled`, `overall_score`, `overall_pass` (≥ 0.5).

**Shape/normalization decomposition — Baker-Cousins likelihood ratio.** Per series, on the index-aligned (both-non-null) subset. The full Baker-Cousins statistic decomposes algebraically into a shape-only and a normalization-only piece (the total is their sum exactly, not just asymptotically):

```
λ_total  = 2·Σ [ O·ln(O/E) − (O − E) ]        ~ χ²(N)     goodness of fit
λ_shape  = 2·Σ O·ln(O/Ê)     where Ê = α·E    ~ χ²(N−1)   shape only (α = ΣO/ΣE)
λ_norm   = 2·[ ΣO·ln(ΣO/ΣE) − (ΣO − ΣE) ]     ~ χ²(1)     total only
```

Each λ yields a p-value via `scipy.stats.chi2.sf(λ, dof)` and an effective sigma `z = √λ`. The **rubric-feeding score** is the bounded monotone `exp(−z / 5)` — gentler than the raw p-value (which saturates at zero for modest deviations on high-stat samples), still a calibrated statistical quantity. At `z=5` → 0.37; `z=10` → 0.14.

Per-series fields in `score.json`:

- `shape`: `{lambda, dof, lambda_per_dof, z, p_value, score}` + nested `ks: {stat, p_value, n_eff}` as a secondary diagnostic
- `normalization`: `{lambda, dof=1, z, p_value, score, ratio, log10_ratio}` — ratio and log10 ratio kept for human readability (physicists read "2× off" more naturally than "z=8.3")
- `total`: `{bc_stat, dof, z, p_value}` — the full GoF statistic
- `combined` = √(shape.score · norm.score)
- `diagnosis`: `GOOD` / `SHAPE OK, NORM BAD` / `SHAPE BAD, NORM OK` / `BOTH BAD`

Paper-level rollups: `overall_shape`, `overall_normalization`, `overall_combined` (means of per-series rubric scores).

**Why BC, not Pearson?** BC reduces to Pearson χ² at high counts (Taylor expansion of `2·O·ln(O/E) − 2(O−E)` around `O=E` is `(O−E)²/E + O(…)³`) but handles low/zero-count bins correctly: `0·ln(0) ≡ 0`, no divide-by-zero, still asymptotically χ²-distributed. In HEP histograms with mixed high-yield peaks and near-zero tails it's the safer default.

**Caveat on the Poisson assumption.** Our "observations" are weighted yields (σ×L×ε), not integer event counts. BC's log-likelihood is derived for Poisson data; using it on weighted yields is the standard HEP approximation and fine for ranking. A more rigorous variant transforms yields to effective counts `N_eff = yield² / σ²_yield` using published errors; we may add that if low-stat series show systematic bias.

```bash
python -m LHCRecastBench.evaluation.score 1707.06193 --recast-dir <ws>/HEPRecastData
python -m LHCRecastBench.evaluation.score 1707.06193 --compare <ws1>/HEPRecastData <ws2>/HEPRecastData
```

Note: arXiv ID is **positional**.

### 2. `rubric_scorer.py` — artifact + process scoring

Weighted checkpoint rubric (PaperBench-style) + cost/token accounting. Emits `eval/rubric_scorer.json`.

| Checkpoint | Weight | What's measured |
|---|---:|---|
| Code executes | 15% | Filled `HEPRecastData/` *and* non-trivial `analysis.py` / `analysis/**/*.py` (size aggregated) |
| Datasets discovered | 15% | Fraction of entries in `datasets.yaml` with `file_urls` *or* `file_dirs` (locally generated signals count); plus how many have `cross_section_pb` |
| Event selection (shape) | 20% | `overall_shape` from `score.py` |
| Normalization | 20% | `overall_normalization` from `score.py` |
| Yield within tolerance | 20% | `overall_score` from `score.py` |
| Documentation | 10% | `report.md` size: >2 KB → 1.0, >500 B → 0.7, >100 B → 0.3 |

Also extracts from the session log: API cost (USD), wall/API duration, input/output/cache tokens, turn count, tool-call breakdown, error count, analysis runs, and derived efficiency metrics ($/point, tokens/point, error rate).

```bash
python -m LHCRecastBench.evaluation.rubric_scorer --arxiv 1707.06193 --agent-dir <ws>
```

### 3. `plot_recast.py` — histogram comparisons (visual)

Produces per-table PNGs under `eval/plots/`. For each reference YAML it emits two figures:

- `<stem>_yield.png` — absolute event yields (`Events / <unit>`), bin-width-divided so variable binning is honest
- `<stem>_shape.png` — unit-area normalised (same, per unit area)

Each figure has a top panel with step histograms (CMS solid, Recast dashed, one color per series) and a ratio sub-panel plotting `recast / CMS` per bin with a horizontal line at 1. Error bars come from the reference `symerror`. Uses `mplhep.style.CMS`; no titles, legend inside the top panel. Series with both arrays all-zero are skipped.

```bash
python -m LHCRecastBench.evaluation.plot_recast --arxiv 1707.06193 --recast-dir <ws>/HEPRecastData
```

### 4. `llm_judge.py` — reasoning quality + provenance verification

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
python -m LHCRecastBench.evaluation.llm_judge --agent-dir <ws> --arxiv 1707.06193
```

## Output layout

After all four have run, `eval/` contains:

```
eval/
  score.json                           # per-bin pulls + shape/norm decomposition (single file)
  rubric_scorer.json                   # checkpoint rubric + cost/tokens
  plots/                               # step-histogram PNGs (CMS vs recast, yield + shape)
    <table>_yield.png
    <table>_shape.png
  judge_scores.json                    # 6-dim reasoning scores + provenance
  judge_failure_report.md              # typed failure catalog
  HEPRecastData_corrected_by_judge/    # only if judge applied corrections
  score_corrected.json                 # score.py re-run on the corrected data
```

## Key design decisions

- **One scorer, not two.** Per-bin pulls and shape/norm decomposition are produced in a single pass over the YAMLs (`LHCRecastBench/evaluation/score.py`). One file loaded, one JSON written, one place to audit the math.
- **Errors are total, not stat-only.** The reference YAMLs carry a `label: stat+syst` (or `total`) symerror that already represents the full uncertainty. The pull denominator is that value — no extra Poisson term added on top.
- **No integer floor on Poisson fallback.** When no error is stated, `sqrt(|ref_val|)` is used verbatim. Reference values are rescaled expected yields (often fractional), so flooring would make every small-yield bin trivially pass.
- **Bin alignment is by-index, not by-filter.** A joint mask over (ref, rec) pairs preserves bin-to-bin correspondence on sparse references.
- **Zero-expectation bins are scored, not skipped.** `ref == 0` demands `rec ≈ 0`; wrong predictions fail rather than being silently dropped.
- **Shape is the BC likelihood ratio, not Pearson χ².** BC reduces to Pearson at high counts but is well-defined at zero-expectation bins and is asymptotically χ²-distributed at lower counts. Per-bin uncertainty enters via the Poisson likelihood itself — no explicit `Var = Poisson + sys²` term is needed.
- **Rubric score is a bounded z-score, not a raw p-value.** `rubric = exp(−√λ / 5)`. The p-value is reported alongside for the statistical reading; the bounded score gives the rubric a usable gradient when p-values saturate at zero.
- **`λ_total = λ_shape + λ_norm` is an algebraic identity, not an asymptotic approximation.** Falls out cleanly from profiling α = ΣO/ΣE over the Poisson log-likelihood.
- **`file_urls` and `file_dirs` both count.** A correctly generated local signal (no xrootd URL, just `sim/<proc>/`) is not penalised as "missing dataset".
- **The OR pass criterion is intentional.** `|pull| < 2 OR rel_diff < 50%`. Either statistical agreement or gross-accuracy agreement suffices. The shape/norm decomposition separates "close enough per bin" from "off by a factor".
- **Multiple evaluators because no single number tells the truth.** `overall_score` is gameable by copying; the judge's provenance check catches that. `score_corrected` is the accuracy number that survives cheating.

## Running all four on a completed workspace

```bash
./launch_eval.sh runs/simulate_1707.06193_claude-opus-4-7_QuantumFeynman_a1b2c3d4
# arXiv ID and task are read from <run_dir>/run_info.json — no need to pass either.
```

Equivalent explicit commands:

```bash
WS=runs/simulate_.../workspace
ARX=1707.06193

python -m LHCRecastBench.evaluation.score         $ARX --recast-dir $WS/HEPRecastData
python -m LHCRecastBench.evaluation.rubric_scorer --arxiv $ARX --agent-dir  $WS
python -m LHCRecastBench.evaluation.plot_recast   --arxiv $ARX --recast-dir $WS/HEPRecastData
python -m LHCRecastBench.evaluation.llm_judge     --arxiv $ARX --agent-dir  $WS
```

The first runs automatically at the end of every agent launch; the other three are manual (or all at once via `launch_eval.sh`).
