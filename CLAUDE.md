# Environment

The wrapper tools (`hepdata`, `cms-opendata`, `read-paper`, `run-analysis`) handle their own environment activation. Call them directly — do not prepend conda activation.

For direct `python3` commands, activate the environment first:

```bash
source /opt/cray/pe/lmod/lmod/init/bash && module load conda && conda activate cms_analysis && python3 script.py
```

# Repository structure

```
LHCRecastBench/          ← THE BENCHMARK (provided to all agents)
  tools/            ← CLI tools, streaming library, preflight, simulation
  evaluation/       ← Offline metrics (score, rubric_scorer, llm_judge, plot_recast)
  papers/           ← PDFs
  BENCHMARK.md      ← Neutral tool reference

agents/
  baseline/         ← OUR AGENT (one implementation)
    runtime/        ← Controller loop, runners, bin wrappers, shell env
    instructions/   ← AGENTS.md, SOUL.md, validation-instructions.md, etc.
```

# Long-running commands

When a Bash command is backgrounded (runs longer than ~2 minutes), you will be **automatically notified** when it completes. The result will be delivered to you without any action on your part.

**Do NOT poll.** Do not run `tail`, `cat`, `wc -l`, `ps aux`, or any other command to check on a backgrounded task. Every poll wastes tokens and context for zero information. Just wait — the notification will come.

While waiting, you may work on other tasks that don't depend on the results.
