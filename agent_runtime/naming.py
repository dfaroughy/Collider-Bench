"""Naming scheme for recast run directories.

Layout:  runs/<runner>_<model>/<task_id>_<Adj><Physicist>_<hex8>/...
Example: runs/claude_opus-4-7/sus-16-046_sim-TChiWg_QuantumFeynman_a1b2c3d4/

The <Adj><Physicist>_<hex8> form is the canonical "agent_id" used in logs,
cross-run comparisons, and the run-directory name.

I/O for run_info.json lives in `agent_runtime.run_info`; usage parsing
in `agent_runtime.usage`; YAML config + task.toml validation in
`agent_runtime.config`. This module is name-generation only.
"""

from __future__ import annotations

import os


PHYSICS_ADJ: list[str] = [
    "Agile",
    "Arcane",
    "Atomic",
    "Bold",
    "Bright",
    "Calm",
    "Candid",
    "Cosmic",
    "Curious",
    "Daring",
    "Deep",
    "Electric",
    "Elegant",
    "Fierce",
    "Flashy",
    "Formal",
    "Gentle",
    "Grand",
    "Grim",
    "Happy",
    "Humble",
    "Rizzy",
    "Keen",
    "Kind",
    "Lively",
    "Boring",
    "Lucid",
    "Lucky",
    "Looney",
    "Merry",
    "Mystic",
    "Nimble",
    "Noble",
    "Romantic",
    "Odd",
    "Malicious",
    "Playful",
    "Prime",
    "Proud",
    "Quantum",
    "Quick",
    "Annoying",
    "Quiet",
    "Radiant",
    "Rapid",
    "Rare",
    "Sharp",
    "Creepy",
    "Extra",
    "Double",
    "Triple",
    "Special",
    "Silly",
    "Sly",
    "Solid",
    "Strange",
    "Swift",
    "Witty",
    "Wise",
    "Oily",
    "Puffy",
    "Goofy",
    "Sweaty",
    "Moldy",
    "Nuclear",
    "Gay",
    "Fuzzy",
    "Sassy",
    "Wacky",
    "Quirky",
    "Cheerful",
    "Slow",
    "Brave",
    "Clever",
    "Eccentric",
    "Funky",
    "Sad",
    "Salty",
    "Spicy",
    "Bubbly",
    "Stubborn",
    "Zany",
    "Jolly",
    "Mysterious",
    "Nerdy",
    "Peculiar",
    "Papi",
    "Mami",
    "Cool",
    "Geeky",
    "Chubby",
    "Fatty",
    "Skinny",
    "Fluffy",
    "Anti",
    "Pro",
    "Super",
    "Farty",
]

PHYSICIST_LAST: list[str] = [
    "Einstein",
    "Feynman",
    "Pauli",
    "Dirac",
    "Bohr",
    "Curie",
    "Nielsen",
    "Mayer",
    "Planck",
    "Newton",
    "Maxwell",
    "Faraday",
    "Galilei",
    "Kepler",
    "Englert",
    "Kibble",
    "Higgs",
    "Bell",
    "Anderson",
    "Schrodinger",
    "Heisenberg",
    "Boltzmann",
    "Noether",
    "Hawking",
    "Tesla",
    "Rutherford",
    "Lorentz",
    "Hertz",
    "Laplace",
    "vonNeumann",
    "Weisskopf",
    "Dyson",
    "Lagrange",
    "Gauss",
    "Quinn",
    "Huygens",
    "Ampere",
    "Volta",
    "Ohm",
    "Compton",
    "deBroglie",
    "Salam",
    "Rubin",
    "Bose",
    "Fermi",
    "Weyl",
    "Majorana",
    "Mach",
    "Born",
    "Sommerfeld",
    "Wu",
    "Rydberg",
    "Zeldovich",
    "Chandrasekhar",
    "Witten",
    "Bhabha",
    "Meitner",
    "Bethe",
    "Cherenkov",
    "Yang",
    "Wigner",
    "GellMann",
    "Landau",
    "Poincare",
    "Geiger",
    "Alcubierre",
    "Penrose",
    "Susskind",
    "tHooft",
    "Weinberg",
    "Glashow",
    "Gerlach",
    "Cabibbo",
    "Millikan",
    "Yukawa",
    "Georgi",
    "Nambu",
    "Wilson",
    "Gibbs",
    "Hamilton",
    "Poisson",
    "Chadwick",
    "Gamow",
    "Kobayashi",
    "Maskawa",
    "Gross",
    "Wilczek",
    "Veltman",
    "Ehrenfest",
    "Sakharov",
    "Fadeev",
    "Popov",
    "Zwicky",
    "Oppenheimer",
    "Kramers",
    "Adler",
    "Bardeen",
    "Bjorken",
    "Drell",
    "Uhlenbeck",
    "Politzer",
    "Reines",
    "Perl",
    "Rubbia",
    "Alvarez",
    "Tomonaga",
    "Schwinger",
    "Kadanoff",
    "Stuckelberg",
    "Segre",
    "Lee",
    "Lawrence",
    "Wien",
    "Bragg",
    "Raman",
    "Becquerel",
    "Rontgen",
    "Pauling",
    "Goldstone",
]


def physicist_bigram(hash_hex: str) -> str:
    """Build `<Adj><Physicist>_<hex8>` from a hex string (min 8 chars)."""
    a = int(hash_hex[:2], 16) % len(PHYSICS_ADJ)
    b = int(hash_hex[2:4], 16) % len(PHYSICIST_LAST)
    short = hash_hex[:8]
    return f"{PHYSICS_ADJ[a]}{PHYSICIST_LAST[b]}_{short}"


def _normalize_model_name(raw: str) -> str:
    return raw.replace("/", "-").replace(" ", "-")


def run_group(runner_name: str, model_name: str) -> str:
    """Directory slug grouping runs by (runner, model).

    Strips a duplicated runner prefix from the model so
    `runner=claude, model=claude-opus-4-7` becomes `claude_opus-4-7`
    rather than `claude_claude-opus-4-7`.
    """
    if not model_name:
        return runner_name
    m = _normalize_model_name(model_name)
    prefix = runner_name + "-"
    if m.startswith(prefix):
        m = m[len(prefix) :]
    return f"{runner_name}_{m}"


def generate_run_info(
    task_id: str,
    agent_name: str,
    runner_name: str,
    model_name: str,
    paper_ref: str | None = None,
) -> dict:
    """Return a dict with the canonical run metadata.

    Keys:
      agent_id  — <Adj><Physicist>_<hex8>      (canonical short name)
      run_dir   — <runner>_<model>/<task_id>_<Adj><Physicist>_<hex8>
      task_id, agent, runner, model, paper_ref (echoed for convenience)
    """
    hex_hash = os.urandom(8).hex()
    agent_id = physicist_bigram(hex_hash)  # "ElegantFermi_a1b2c3d4"
    group = run_group(runner_name, model_name)
    run_dir = f"{group}/{task_id}_{agent_id}"
    return {
        "agent_id": agent_id,
        "run_dir": run_dir,
        "task_id": task_id,
        "agent": agent_name,
        "runner": runner_name,
        "model": model_name,
        "paper_ref": paper_ref,
    }
