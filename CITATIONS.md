# Citations

Collider-Bench is built on top of established open-source HEP simulation
software and depends on it for the agent's run-time pipeline. Users of the
benchmark are asked to cite the relevant tool(s) for any work that consumes
their output, in addition to citing the benchmark itself.

The four HEP tools below are baked into the canonical container image at
`/opt/sim/*` (see [`docker/Dockerfile`](docker/Dockerfile)).

## MadGraph5_aMC@NLO

Matrix-element and event generation at parton level (LO and NLO QCD/EW).

> J. Alwall, R. Frederix, S. Frixione, V. Hirschi, F. Maltoni, O. Mattelaer,
> H.-S. Shao, T. Stelzer, P. Torrielli and M. Zaro,
> *"The automated computation of tree-level and next-to-leading order
> differential cross sections, and their matching to parton shower
> simulations"*,
> JHEP **07** (2014) 079.
> [`arXiv:1405.0301`](https://arxiv.org/abs/1405.0301).

Additional citation for mixed-coupling expansions and NLO EW corrections:

> R. Frederix, S. Frixione, V. Hirschi, D. Pagani, H.-S. Shao and M. Zaro,
> *"The automation of next-to-leading order electroweak calculations"*,
> JHEP **07** (2018) 185.
> [`arXiv:1804.10017`](https://arxiv.org/abs/1804.10017).

Source: <https://launchpad.net/mg5amcnlo>

## Pythia 8

Parton showering and hadronization.

> C. Bierlich et al.,
> *"A comprehensive guide to the physics and usage of PYTHIA 8.3"*,
> SciPost Phys. Codebases **8** (2022).
> [`arXiv:2203.11601`](https://arxiv.org/abs/2203.11601).

Source: <https://www.pythia.org>

## Delphes

Fast detector simulation.

> J. de Favereau, C. Delaere, P. Demin, A. Giammanco, V. Lemaître,
> A. Mertens and M. Selvaggi (DELPHES 3 collaboration),
> *"DELPHES 3, A modular framework for fast simulation of a generic collider
> experiment"*,
> JHEP **02** (2014) 057.
> [`arXiv:1307.6346`](https://arxiv.org/abs/1307.6346).

Source: <https://github.com/delphes/delphes> · DOI:
[`10.5281/zenodo.21390046`](https://zenodo.org/badge/latestdoi/21390046).

## Prospino 2.1

NLO QCD cross sections for SUSY pair production.

> W. Beenakker, R. Höpker, M. Spira and P. M. Zerwas,
> *"Squark and gluino production at hadron colliders"*,
> Nucl. Phys. B **492** (1997) 51–103.
> [`doi:10.1016/S0550-3213(97)80027-2`](https://doi.org/10.1016/S0550-3213(97)80027-2).

> W. Beenakker, M. Klasen, M. Krämer, T. Plehn, M. Spira and P. M. Zerwas,
> *"The Production of charginos / neutralinos and sleptons at hadron
> colliders"*,
> Phys. Rev. Lett. **83** (1999) 3780–3783.
> [`doi:10.1103/PhysRevLett.100.029901`](https://doi.org/10.1103/PhysRevLett.100.029901).

Source:
<https://www.thphys.uni-heidelberg.de/~plehn/index.php?show=prospino>

---

## Collider-Bench

If you use Collider-Bench itself in a publication or evaluation, please cite
the dataset record on HuggingFace (CITATION.cff included with the corpus):

> D. A. Faroughy, S. Palacios Schweitzer, I. Pang, S. Mishra-Sharma and
> D. Shih,
> *Collider-Bench: A benchmark for LHC analysis recasting by LLM agents*,
> 2026.
> <https://huggingface.co/datasets/Dariusfar/ColliderBench>

The GitHub repository at <https://github.com/dfaroughy/Collider-Bench> hosts
the runtime harness, scorer, and task corpus.
