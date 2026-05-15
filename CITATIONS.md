# Citations


## Collider-Bench

If you use Collider-Bench itself in a publication or evaluation, please cite us under:

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

Dataset: <https://huggingface.co/datasets/Dariusfar/ColliderBench>

GitHub: <https://github.com/dfaroughy/Collider-Bench>

---

Collider-Bench is built on top of established open-source HEP simulation
software and depends on it for the agent's run-time pipeline. Users of the
benchmark are asked to cite the relevant tool(s) for any work that consumes
their output, in addition to citing the benchmark itself.

The four HEP tools below are baked into the canonical container image at
`/opt/sim/*` (see [`docker/Dockerfile`](docker/Dockerfile)).

## MadGraph5_aMC@NLO

Matrix-element and event generation at parton level (LO and NLO QCD/EW).

```bibtex
@article{Alwall:2014hca,
    author = "Alwall, J. and Frederix, R. and Frixione, S. and Hirschi, V. and Maltoni, F. and Mattelaer, O. and Shao, H.-S. and Stelzer, T. and Torrielli, P. and Zaro, M.",
    title = "{The automated computation of tree-level and next-to-leading order differential cross sections, and their matching to parton shower simulations}",
    eprint = "1405.0301",
    archivePrefix = "arXiv",
    primaryClass = "hep-ph",
    doi = "10.1007/JHEP07(2014)079",
    journal = "JHEP",
    volume = "07",
    pages = "079",
    year = "2014"
}
```

Additional citation for mixed-coupling expansions and NLO EW corrections:

```bibtex
@article{Frederix:2018nkq,
    author = "Frederix, R. and Frixione, S. and Hirschi, V. and Pagani, D. and Shao, H.-S. and Zaro, M.",
    title = "{The automation of next-to-leading order electroweak calculations}",
    eprint = "1804.10017",
    archivePrefix = "arXiv",
    primaryClass = "hep-ph",
    doi = "10.1007/JHEP07(2018)185",
    journal = "JHEP",
    volume = "07",
    pages = "185",
    year = "2018"
}
```

Source: <https://launchpad.net/mg5amcnlo>

## Pythia 8

Parton showering and hadronization.

```bibtex
@article{Bierlich:2022pfr,
    author = "Bierlich, Christian and others",
    title = "{A comprehensive guide to the physics and usage of PYTHIA 8.3}",
    eprint = "2203.11601",
    archivePrefix = "arXiv",
    primaryClass = "hep-ph",
    doi = "10.21468/SciPostPhysCodeb.8",
    journal = "SciPost Phys. Codebases",
    pages = "8",
    year = "2022"
}
```

Source: <https://www.pythia.org>

## Delphes

Fast detector simulation.

```bibtex
@article{deFavereau:2013fsa,
    author = "de Favereau, J. and Delaere, C. and Demin, P. and Giammanco, A. and Lemaitre, V. and Mertens, A. and Selvaggi, M.",
    collaboration = "DELPHES 3",
    title = "{DELPHES 3, A modular framework for fast simulation of a generic collider experiment}",
    eprint = "1307.6346",
    archivePrefix = "arXiv",
    primaryClass = "hep-ex",
    doi = "10.1007/JHEP02(2014)057",
    journal = "JHEP",
    volume = "02",
    pages = "057",
    year = "2014"
}
```

Source: <https://github.com/delphes/delphes>

## Prospino 2.1

NLO QCD cross sections for SUSY pair production.

```bibtex
@article{Beenakker:1996ch,
    author = "Beenakker, W. and Hopker, R. and Spira, M. and Zerwas, P. M.",
    title = "{Squark and gluino production at hadron colliders}",
    eprint = "hep-ph/9610490",
    archivePrefix = "arXiv",
    doi = "10.1016/S0550-3213(97)80027-2",
    journal = "Nucl. Phys. B",
    volume = "492",
    pages = "51--103",
    year = "1997"
}

@article{Beenakker:1999xh,
    author = "Beenakker, W. and Klasen, M. and Kramer, M. and Plehn, T. and Spira, M. and Zerwas, P. M.",
    title = "{The Production of charginos / neutralinos and sleptons at hadron colliders}",
    eprint = "hep-ph/9906298",
    archivePrefix = "arXiv",
    doi = "10.1103/PhysRevLett.83.3780",
    journal = "Phys. Rev. Lett.",
    volume = "83",
    pages = "3780--3783",
    year = "1999",
    note = "[Erratum: Phys.Rev.Lett. 100, 029901 (2008)]"
}
```

Source: <https://www.thphys.uni-heidelberg.de/~plehn/index.php?show=prospino>
