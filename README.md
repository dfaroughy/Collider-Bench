
![Collider-Bench](artifacts/Collider-Bench.png)
![Collider-Bench diagram](artifacts/Collider-Bench_diagram.png)

**Collider-Bench** is an AI  benchmark for evaluating whether LLM agents can reproduce experimental analyses from the **Large Hadron Collider** (LHC) at **CERN** using only public papers and open scientific software.

Each task requires multi-step scientific reasoning by an autonomous coding agent, from reading a published CMS or ATLAS search and identifying the relevant signal region, to generating and processing simulated signal events, implementing the event selection, and predicting the binned signal yields reported by the analysis

The benchmark tests long-horizon scientific reasoning under realistic conditions, including ambiguous or underspecified paper descriptions, underdocumented domain-specific tools and approximate public simulation pipelines.

arXiv preprint: [2605.13950](https://arxiv.org/abs/2605.13950)

## Quick start

The HEP simulation stack (MadGraph, Pythia8, Delphes, Prospino) lives entirely
inside the prebuilt container image.

Clone and install the harness into a venv

```
git clone https://github.com/dfaroughy/Collider-Bench.git
cd Collider-Bench
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Pull the prebuilt benchmark image via podman | docker | apptainer

```
podman pull    ghcr.io/dfaroughy/lhc-bench:latest
docker pull    ghcr.io/dfaroughy/lhc-bench:latest
apptainer pull docker://ghcr.io/dfaroughy/lhc-bench:latest
```

Install the vendor agents on the host and export the API keys
```
npm i -g @anthropic-ai/claude-code    # Claude Code → ~/.local/bin/claude
npm i -g @openai/codex                # Codex CLI    → ~/.local/bin/codex
export ANTHROPIC_API_KEY=...          # for --runner claude
export OPENAI_API_KEY=...          # for --runner codex
```

Run task
```
scripts/run-agent --config configs/claude.yaml --task sus-16-046_sim-T5Wg
```

The image is OCI-compliant —  `podman`, `docker`, `apptainer`, `singularity` all
work against it.

## Tasks

Tasks live under [`ColliderBench/tasks/`](ColliderBench/tasks/). Each task
has a `TASK.md`, `task.toml`, and a null-filled `template/*.yaml` copied into
the run workspace as `results/*.yaml`.

Each `sim` task asks the agent to reproduce the published per-bin yield
distribution. Scoring is a single primary metric — the relative L² distance
$d(\hat y, y^\star)$ between the agent's bin yields $\hat y$ and the published
reference $y^\star$.

| Task id | Analysis target | Signal | Observable | Paper | Plot units |
|---|---|---|---|---|---|
| `sus-16-034_sim-TChiWZ`        | leptons + jets | `TChiWZ`              | $E_T^{\rm miss}$ | CMS-SUS-16-034<sup>1</sup> | Events/bin |
| `sus-16-046_sim-T5Wg`          | photons        | `T5Wg`                | $S_T^{\gamma}$   | CMS-SUS-16-046<sup>2</sup> | Events/GeV |
| `sus-16-046_sim-TChiWg`        | photons        | `TChiWg`              | $S_T^{\gamma}$   | CMS-SUS-16-046<sup>2</sup> | Events/bin |
| `sus-16-047_sim-T5Wg_highHT`   | photons        | `T5Wg`, high-$H_T$    | $p_T^{\rm miss}$ | CMS-SUS-16-047<sup>3</sup>  | Events/bin |
| `sus-16-047_sim-T5Wg_lowHT`    | photons        | `T5Wg`, low-$H_T$     | $p_T^{\rm miss}$ | CMS-SUS-16-047<sup>3</sup> | Events/bin |
| `sus-16-047_sim-T6gg_highHT`   | photons        | `T6gg`, high-$H_T$    | $p_T^{\rm miss}$ | CMS-SUS-16-047<sup>3</sup> | Events/bin |
| `sus-16-047_sim-T6gg_lowHT`    | photons        | `T6gg`, low-$H_T$     | $p_T^{\rm miss}$ | CMS-SUS-16-047<sup>3</sup> | Events/bin |
| `sus-16-051_sim-T2tt_SRG`      | single lepton  | `T2tt`                | $E_T^{\rm miss}$ | CMS-SUS-16-051<sup>4</sup> | Events/bin |
| `sus-16-051_sim-T2bW_SRG`      | single lepton  | `T2bW`                | $E_T^{\rm miss}$ | CMS-SUS-16-051<sup>4</sup> | Events/bin |
| `sus-16-051_sim-T2tt_comp`     | single lepton  | `T2tt`, compressed    | $E_T^{\rm miss}$ | CMS-SUS-16-051<sup>4</sup> | Events/bin |

### Running one task
Launches the agent CLI, scores the result, writes `runs/<runner>_<model>/<task>/`.

```bash
scripts/run-agent --config configs/claude.yaml \
                  --task sus-16-047_sim-T5Wg_lowHT
```

### Re-scoring or judging an existing run

Offline scoring with LLM-judge option:
```bash
scripts/launch_eval.sh runs/<path>
scripts/launch_eval.sh runs/<path>  --judge
```

## Sandboxing

Agents run inside a pluggable sandbox — see [`agent_runtime/SANDBOX.md`](agent_runtime/SANDBOX.md).

The agent runs inside the canonical `lhc-bench` image:
`workspace/` is rw-bound, and container backends expose only the benchmark
surfaces the agent needs: `ColliderBench/tools/`, `ColliderBench/bin/`, and
the resolved paper directory. Hidden reference data and evaluator code are not
mounted into the agent container.

## Configs

Two public reference configs live under [`configs/`](configs/):

| Config | When to use |
|---|---|
| [`configs/claude.yaml`](configs/claude.yaml)            | Single-host runs on any Linux box, Mac with Podman Desktop, or cloud VM. `compute: local`, `sandbox: podman`. |
| [`configs/claude_slurm.yaml`](configs/claude_slurm.yaml) | SLURM allocation on Perlmutter / a similar cluster. `compute: slurm` plus the usual allocation fields. |

Both files are self-contained — copy and adapt. Full schema, validation
rules, and the runtime path from YAML to launched process are documented in
[`configs/CONFIG.md`](configs/CONFIG.md).

Minimum viable config:

```yaml
agent:    simple
task:     sus-16-046_sim-T5Wg
runner:   claude              # claude | codex | gemini | aider | forge
auth:     api                 # or oauth
model:    claude-opus-4-7
sandbox:  podman              # podman | docker | apptainer | singularity | none
compute:  local               # or slurm (+ allocation fields)
```

CLI flags on `scripts/run-agent` override config values.

## Continuous integration

[`.github/workflows/test.yml`](.github/workflows/test.yml) runs on every push and PR to `main`:

- **`pytest`** on Python 3.10 / 3.11 / 3.12 (matrix).
- **`pre-commit`** on all files (fails the job if any hook would make a change).


## Citation

If you use Collider-Bench in a publication, please cite us.

```bibtex
@article{Faroughy:2026dkj,
    author = "Faroughy, Darius A. and Palacios Schweitzer, Sofia and Pang, Ian and Mishra-Sharma, Siddharth and Shih, David",
    title = "{Collider-Bench: Benchmarking AI Agents with Particle Physics Analysis Reproduction}",
    eprint = "2605.13950",
    archivePrefix = "arXiv",
    primaryClass = "cs.LG",
    month = "5",
    year = "2026"
}
```

The HEP tool reference list is in [`CITATIONS.md`](CITATIONS.md).

## References
<sup>1</sup> Albert M Sirunyan et al. Search for new phenomena in final states with two opposite-charge, same-
flavor leptons, jets, and missing transverse momentum in pp collisions at √s = 13 TeV. JHEP,
03:076, 2018b. doi: 10.1007/s13130-018-7845-2

<sup>2</sup> Albert M Sirunyan et al. Search for gauge-mediated supersymmetry in events with at least one
photon and missing transverse momentum in pp collisions at √s = 13 TeV. Phys. Lett. B, 780:
118–143, 2018a. doi: 10.1016/j.physletb.2018.02.045

<sup>3</sup> Albert M Sirunyan et al. Search for supersymmetry in events with at least one photon, missing
transverse momentum, and large transverse event activity in proton-proton collisions at √s = 13
TeV. JHEP, 12:142, 2017b. doi: 10.1007/JHEP12(2017)142

<sup>4</sup> Albert M Sirunyan et al. Search for top squark pair production in pp collisions at √s = 13 TeV
using single lepton events. JHEP, 10:019, 2017a. doi: 10.1007/JHEP10(2017)019
