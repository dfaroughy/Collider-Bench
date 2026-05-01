"""Pre-flight checks and post-flight summary for analysis.py.

Called automatically by runtime/bin/run-analysis — the agent does not need to import this.
"""

import ast
import json
import sys
import time
import yaml
from pathlib import Path


# ── AST-based code analysis ──────────────────────────────────────────────────


class AnalysisChecker(ast.NodeVisitor):
    """Walk the AST of analysis.py and flag performance/correctness issues."""

    def __init__(self, source_lines: list[str]):
        self.lines = source_lines
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.imports: set[str] = set()
        self.has_datasets_yaml_ref = False

    def visit_Import(self, node):
        for alias in node.names:
            self.imports.add(alias.name.split(".")[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module:
            self.imports.add(node.module.split(".")[0])
        self.generic_visit(node)

    def visit_Constant(self, node):
        # Check for hardcoded HTTPS URLs to eospublic
        if isinstance(node.value, str):
            if "https://eospublic" in node.value:
                self.errors.append(
                    f"line {node.lineno}: Hardcoded HTTPS URL to eospublic.cern.ch. "
                    "Use root:// xrootd URIs from datasets.yaml instead."
                )
            if "datasets.yaml" in node.value:
                self.has_datasets_yaml_ref = True
        self.generic_visit(node)

    def visit_For(self, node):
        # Detect: for <var> in range(n_...) or range(len(...))
        if isinstance(node.iter, ast.Call) and isinstance(node.iter.func, ast.Name):
            if node.iter.func.id == "range" and node.iter.args:
                arg = node.iter.args[0]
                flagged = False

                # range(n_events), range(n_pass), range(n_ev), etc.
                if isinstance(arg, ast.Name) and (
                    arg.id.startswith("n_") or arg.id in ("n_events", "n_pass", "num_events")
                ):
                    flagged = True

                # range(len(something))
                if (
                    isinstance(arg, ast.Call)
                    and isinstance(arg.func, ast.Name)
                    and arg.func.id == "len"
                ):
                    # Check if it's iterating over events-sized arrays
                    if arg.args and isinstance(arg.args[0], ast.Name):
                        varname = arg.args[0].id
                        # Heuristic: if the variable suggests events/leptons/jets
                        if any(
                            kw in varname.lower()
                            for kw in ("ev", "event", "lep", "mu", "el", "jet", "pass")
                        ):
                            flagged = True

                if flagged:
                    line_text = self.lines[node.lineno - 1].strip()
                    self.errors.append(
                        f"line {node.lineno}: `{line_text[:80]}` — "
                        "event-level Python for-loop. Use vectorized awkward array operations "
                        "(ak.combinations, ak.argmin, boolean masking)."
                    )

        self.generic_visit(node)

    def finalize(self):
        """Run checks that need the full picture."""
        if "uproot" not in self.imports:
            self.warnings.append("analysis.py does not import uproot.")

        if not self.has_datasets_yaml_ref:
            self.warnings.append(
                "analysis.py does not reference 'datasets.yaml'. "
                "File URLs must come from datasets.yaml, not be hardcoded."
            )


def check_code(analysis_path: str) -> tuple[list[str], list[str]]:
    """Parse analysis.py and return (errors, warnings)."""
    path = Path(analysis_path)
    if not path.exists():
        return ["analysis.py not found."], []

    source = path.read_text()
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"analysis.py has a syntax error: {e}"], []

    lines = source.split("\n")
    checker = AnalysisChecker(lines)
    checker.visit(tree)
    checker.finalize()
    return checker.errors, checker.warnings


# ── Dataset checks ───────────────────────────────────────────────────────────


def check_datasets(datasets_path: str) -> tuple[list[str], list[str], dict]:
    """Check datasets.yaml and return (errors, warnings, summary)."""
    errors = []
    warnings = []
    summary = {"total_files": 0, "total_events": 0, "active": [], "blocked": []}

    path = Path(datasets_path)
    if not path.exists():
        return ["datasets.yaml not found."], [], summary

    with open(path) as f:
        datasets = yaml.safe_load(f) or []

    if not datasets:
        return ["datasets.yaml is empty."], [], summary

    for d in datasets:
        status = d.get("status", "")
        n_files = len(d.get("file_urls", []))

        if status.startswith("BLOCKED"):
            summary["blocked"].append(d["process"])
            continue

        summary["active"].append(d["process"])
        summary["total_files"] += n_files
        summary["total_events"] += d.get("events", 0) or 0

        # HTTPS check
        if any(u.startswith("https://") for u in d.get("file_urls", [])):
            errors.append(
                f"{d['process']}: HTTPS URLs found. Use root:// xrootd URIs "
                "(HTTPS fails with SSL on NERSC)."
            )

        # Missing cross section for MC
        if d.get("role") != "data" and not d.get("cross_section_pb"):
            warnings.append(f"{d['process']}: missing cross_section_pb, yields won't normalize.")

        # No files
        if n_files == 0:
            warnings.append(f"{d['process']}: 0 file URLs.")

    if summary["total_files"] == 0:
        errors.append("No file URLs in any active dataset.")

    if summary["total_files"] > 50:
        warnings.append(
            f"Large dataset: {summary['total_files']} files, "
            f"{summary['total_events']:,} events. "
            "Use vectorized operations and parallel file processing."
        )

    return errors, warnings, summary


# ── Main entry points ────────────────────────────────────────────────────────


def check_analysis(datasets_path: str, analysis_path: str):
    """Run all pre-flight checks. Hard-fails on errors."""
    all_errors = []
    all_warnings = []

    # Dataset checks
    ds_errors, ds_warnings, summary = check_datasets(datasets_path)
    all_errors.extend(ds_errors)
    all_warnings.extend(ds_warnings)

    # Code checks
    code_errors, code_warnings = check_code(analysis_path)
    all_errors.extend(code_errors)
    all_warnings.extend(code_warnings)

    # Print dataset summary
    print(f"\n{'─' * 60}")
    print("  DATASET SUMMARY")
    print(f"{'─' * 60}")
    with open(datasets_path) as f:
        for d in yaml.safe_load(f) or []:
            status = d.get("status", "")
            n = len(d.get("file_urls", []))
            tag = f" [{status}]" if status else ""
            print(f"  {d['process']:30s} {d.get('role', '?'):10s} {n:4d} files{tag}")
    print(f"{'─' * 60}")
    print(f"  Active: {summary['total_files']} files, {summary['total_events']:,} events")
    if summary["blocked"]:
        print(f"  Blocked: {', '.join(summary['blocked'])}")
    print(f"{'─' * 60}\n")

    # Print warnings
    for w in all_warnings:
        print(f"  WARNING: {w}", file=sys.stderr)

    # Print errors and fail
    if all_errors:
        for e in all_errors:
            print(f"  ERROR: {e}", file=sys.stderr)
        print(
            f"\n  Pre-flight failed ({len(all_errors)} errors). Fix before running.\n",
            file=sys.stderr,
        )
        sys.exit(1)

    print("  Pre-flight passed.\n")


def print_postflight(start_time: float, results_path: str = "results.json"):
    """Print a summary after the analysis completes."""
    elapsed = time.time() - start_time
    minutes = elapsed / 60

    print(f"\n{'─' * 60}")
    print(f"  ANALYSIS COMPLETE — {minutes:.1f} min")
    print(f"{'─' * 60}")

    results = Path(results_path)
    if results.exists():
        try:
            data = json.loads(results.read_text())
            primary = data.get("primary_observable", {})
            if primary.get("value") is not None:
                print(f"  Primary: {primary.get('name', '?')} = {primary['value']}")
            cutflow = data.get("cutflow", {})
            if cutflow:
                print(f"  Cutflow samples: {', '.join(cutflow.keys())}")
        except Exception:
            pass

    required = [
        "report.md",
    ]
    missing = [f for f in required if not Path(f).exists()]
    if missing:
        print(f"  MISSING ARTIFACTS: {', '.join(missing)}")
    else:
        print("  All required artifacts present.")
    print(f"{'─' * 60}\n")
