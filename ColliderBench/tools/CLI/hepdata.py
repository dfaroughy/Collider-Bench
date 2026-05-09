#!/usr/bin/env python3
"""
CLI for HEPData operations.

Usage:
    python3.11 -m tools.CLI.hepdata find 1607.08834
    python3.11 -m tools.CLI.hepdata tables 1478600
    python3.11 -m tools.CLI.hepdata get 1478600 "Table 1"
    python3.11 -m tools.CLI.hepdata download 1478600 --output-dir HEPData/
"""

import argparse
import json
import os
import ssl
import sys
import tarfile
from pathlib import Path
from urllib.request import urlopen, Request

try:
    ssl._create_default_https_context = ssl._create_unverified_context
except Exception:
    pass

HEPDATA_API = "https://www.hepdata.net/api"
HEPDATA_SEARCH = "https://www.hepdata.net/search/"
HEPDATA_RECORD = "https://www.hepdata.net/record/"


def _print_json(payload):
    try:
        print(json.dumps(payload, indent=2, sort_keys=True))
    except BrokenPipeError:
        pass


def _api_get(url, retries=3, backoff=5):
    import time

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = Request(url)
    for attempt in range(retries):
        try:
            with urlopen(req, timeout=30, context=ctx) as resp:
                return json.loads(resp.read())
        except Exception as exc:
            code = getattr(exc, "code", None)
            if attempt < retries - 1 and (code in (502, 503, 504) or "timeout" in str(exc).lower()):
                wait = backoff * (attempt + 1)
                print(f"HEPData unavailable ({exc}), retrying in {wait}s...", file=sys.stderr)
                time.sleep(wait)
            else:
                raise


def _parse_header_name_units(raw):
    """Parse 'Name [units]' into (name, units)."""
    raw = raw.strip()
    if "[" in raw and raw.endswith("]"):
        idx = raw.index("[")
        name = raw[:idx].strip()
        units = raw[idx + 1 : -1].strip()
        return name, units
    return raw, ""


def _convert_record_data_json(data):
    """Convert the /record/data/ JSON schema (headers + flat values rows with x/y lists)
    into the traditional independent_variables / dependent_variables schema."""
    if "independent_variables" in data or "headers" not in data or "values" not in data:
        return data  # already in traditional format or not the new format

    headers = data["headers"]
    values = data["values"]

    # Separate x-headers and y-headers
    x_headers = [
        h for h in headers if h.get("is_independent", h.get("type", "")) in (True, "independent")
    ]
    y_headers = [
        h for h in headers if h.get("is_independent", h.get("type", "")) in (False, "dependent")
    ]

    # Fallback: if type flags aren't present, infer from the first row
    if not x_headers and not y_headers and values:
        first_row = values[0]
        n_x = len(first_row.get("x", []))
        n_y = len(first_row.get("y", []))
        x_headers = headers[:n_x]
        y_headers = headers[n_x : n_x + n_y]

    # Build independent_variables
    independent_variables = []
    for i, hdr in enumerate(x_headers):
        name, units = _parse_header_name_units(hdr.get("name", f"x{i}"))
        iv = {"header": {"name": name, "units": units}, "values": []}
        for row in values:
            x_entries = row.get("x", [])
            if i < len(x_entries):
                entry = x_entries[i]
                if "low" in entry and "high" in entry:
                    iv["values"].append({"low": entry["low"], "high": entry["high"]})
                else:
                    iv["values"].append({"value": entry.get("value")})
        independent_variables.append(iv)

    # Build dependent_variables
    dependent_variables = []
    for j, hdr in enumerate(y_headers):
        name, units = _parse_header_name_units(hdr.get("name", f"y{j}"))
        dv = {
            "header": {"name": name, "units": units},
            "qualifiers": hdr.get("qualifiers", []),
            "values": [],
        }
        for row in values:
            y_entries = row.get("y", [])
            if j < len(y_entries):
                entry = y_entries[j]
                val = {"value": entry.get("value")}
                errors = entry.get("errors", [])
                if errors:
                    val["errors"] = errors
                dv["values"].append(val)
        dependent_variables.append(dv)

    return {
        "independent_variables": independent_variables,
        "dependent_variables": dependent_variables,
    }


def _fetch_full_record(inspire_id):
    """Fetch the full record JSON for an INSPIRE ID."""
    try:
        return _api_get(f"{HEPDATA_RECORD}ins{inspire_id}?format=json")
    except Exception:
        return None


def _summarize_record(rec):
    """Build a structured summary from a full record.

    The HEPData JSON nests metadata under 'record' and tables under 'data_tables'.
    """
    record = rec.get("record", rec)  # nested or flat
    tables = rec.get("data_tables", record.get("data_tables", []))
    table_list = []
    for t in tables:
        entry = {
            "name": t.get("name", ""),
            "description": t.get("title", t.get("description", "")),
        }
        if t.get("doi"):
            entry["doi"] = t["doi"]
        formats = list(t.get("data", {}).keys()) if "data" in t else t.get("formats", [])
        if formats:
            entry["formats"] = formats
        table_list.append(entry)

    return {
        "recid": rec.get("recid", record.get("recid")),
        "inspire_id": record.get("inspire_id"),
        "title": record.get("title", ""),
        "collaborations": record.get("collaborations", []),
        "arxiv_id": record.get("arxiv_id", ""),
        "doi": record.get("doi", ""),
        "year": record.get("year"),
        "num_tables": len(tables),
        "tables": table_list,
    }


def cmd_find(args):
    """Find a HEPData record."""
    query = args.query
    from urllib.parse import quote

    # If query looks like an INSPIRE ID, try direct fetch first
    inspire_id = None
    if query.startswith("ins") and query[3:].isdigit():
        inspire_id = query[3:]
    elif query.isdigit() and len(query) >= 5:
        inspire_id = query

    if inspire_id:
        rec = _fetch_full_record(inspire_id)
        if rec and rec.get("title"):
            summary = _summarize_record(rec)
            if args.json:
                _print_json({"query": query, "total": 1, "results": [summary]})
                return
            print(f"Record ins{inspire_id}:")
            print(f"  Title: {summary['title']}")
            print(f"  Collaborations: {', '.join(summary['collaborations'])}")
            print(f"  Year: {summary.get('year', '?')}")
            if summary.get("arxiv_id"):
                print(f"  arXiv: {summary['arxiv_id']}")
            if summary.get("doi"):
                print(f"  DOI: {summary['doi']}")
            print(f"  Tables: {summary['num_tables']}")
            for t in summary["tables"]:
                print(f"    {t['name']}: {t['description'][:80]}")
            return

    # Fall back to search
    data = _api_get(f"{HEPDATA_SEARCH}?q={quote(query)}&format=json")

    results = data.get("results", [])
    total = data.get("total", 0)

    # If the first result's arXiv ID matches the query, auto-fetch the full record
    if results and query in str(results[0].get("arxiv_id", "")):
        iid = results[0].get("inspire_id")
        if iid:
            rec = _fetch_full_record(iid)
            if rec:
                summary = _summarize_record(rec)
                if args.json:
                    _print_json(summary)
                    return
                print(f"Record ins{iid}:")
                print(f"  Title: {summary['title']}")
                print(f"  Collaborations: {', '.join(summary.get('collaborations', []))}")
                print(f"  Year: {summary.get('year', '?')}")
                if summary.get("arxiv_id"):
                    print(f"  arXiv: {summary['arxiv_id']}")
                print(f"  Tables: {summary['num_tables']}")
                for t in summary["tables"]:
                    print(f"    {t['name']}: {t['description'][:80]}")
                return

    # If few results, auto-fetch full records
    enriched_results = []
    if 0 < total <= 5:
        for r in results:
            iid = r.get("inspire_id")
            if iid:
                rec = _fetch_full_record(iid)
                if rec and rec.get("title"):
                    enriched_results.append(_summarize_record(rec))
                else:
                    enriched_results.append(r)
            else:
                enriched_results.append(r)
    else:
        enriched_results = results

    if args.json:
        _print_json(
            {
                "query": query,
                "total": total,
                "results": enriched_results,
            }
        )
        return

    print(f"Found {total} records:\n")
    for r in enriched_results[:10]:
        inspire = r.get("inspire_id", "?")
        title = r.get("title", "")[:80]
        collab = r.get("collaborations", [])
        year = r.get("year", "?")
        arxiv = r.get("arxiv_id", "")
        print(f"  ins{inspire} ({year}) [{', '.join(collab)}]")
        print(f"    {title}")
        if arxiv:
            print(f"    arXiv: {arxiv}")
        num_tables = r.get("num_tables")
        if num_tables is not None:
            print(f"    Tables: {num_tables}")
        print()


def cmd_tables(args):
    """List tables in a record."""
    data = _api_get(f"{HEPDATA_RECORD}ins{args.inspire_id}?format=json")
    tables = data.get("data_tables", [])

    if args.json:
        table_entries = []
        for t in tables:
            entry = {
                "name": t.get("name", ""),
                "description": t.get("title", t.get("description", "")),
            }
            if t.get("doi"):
                entry["doi"] = t["doi"]
            if t.get("formats"):
                entry["formats"] = t["formats"]
            table_entries.append(entry)
        _print_json(
            {
                "inspire_id": args.inspire_id,
                "table_count": len(tables),
                "tables": table_entries,
            }
        )
        return

    print(f"Record ins{args.inspire_id}: {len(tables)} tables\n")
    for t in tables:
        name = t.get("name", "")
        desc = t.get("title", t.get("description", ""))[:80]
        doi = t.get("doi", "")
        formats = t.get("formats", [])
        line = f"  {name}: {desc}"
        if doi:
            line += f"  [DOI: {doi}]"
        if formats:
            line += f"  (formats: {', '.join(formats)})"
        print(line)


def cmd_get(args):
    """Fetch a single table's data."""
    from urllib.parse import quote

    url = (
        f"https://www.hepdata.net/download/table/ins{args.inspire_id}/{quote(args.table_name)}/json"
    )
    data = _api_get(url)

    # Detect and convert the new /record/data/ JSON format
    data = _convert_record_data_json(data)

    if args.json:
        _print_json(data)
        return

    indep = data.get("independent_variables", [])
    dep = data.get("dependent_variables", [])

    print(f"Table: {args.table_name}\n")

    if indep or dep:
        # Structured format
        if indep:
            print("  Independent variables:")
            for iv in indep:
                hdr = iv.get("header", {})
                name = hdr.get("name", "?")
                units = hdr.get("units", "")
                label = f"{name} [{units}]" if units else name
                print(f"    {label}")
        if dep:
            print("  Dependent variables:")
            for dv in dep:
                hdr = dv.get("header", {})
                name = hdr.get("name", "?")
                units = hdr.get("units", "")
                label = f"{name} [{units}]" if units else name
                quals = dv.get("qualifiers", [])
                print(f"    {label}")
                for q in quals:
                    print(f"      {q.get('name', '')}: {q.get('value', '')}")

        # Print data rows
        n_rows = max(len(iv.get("values", [])) for iv in indep) if indep else 0
        if not n_rows and dep:
            n_rows = max(len(dv.get("values", [])) for dv in dep)
        print(f"\n  Data ({n_rows} rows, showing up to 20):")
        for i in range(min(n_rows, 20)):
            x_parts = []
            for iv in indep:
                vals = iv.get("values", [])
                if i < len(vals):
                    v = vals[i]
                    if "low" in v and "high" in v:
                        x_parts.append(f"{v['low']}-{v['high']}")
                    else:
                        x_parts.append(str(v.get("value", "?")))
            y_parts = []
            for dv in dep:
                vals = dv.get("values", [])
                if i < len(vals):
                    v = vals[i]
                    val_str = str(v.get("value", "?"))
                    errs = v.get("errors", [])
                    if errs:
                        err_strs = []
                        for e in errs:
                            if "asymerror" in e:
                                err_strs.append(
                                    f"+{e['asymerror'].get('plus', '?')}/-{e['asymerror'].get('minus', '?')}"
                                )
                            elif "symerror" in e:
                                err_strs.append(f"+-{e['symerror']}")
                        val_str += " " + " ".join(err_strs)
                    y_parts.append(val_str)
            print(f"    x=[{', '.join(x_parts)}] y=[{', '.join(y_parts)}]")
    else:
        # Fallback for any other format
        headers = data.get("headers", [])
        values = data.get("values", [])
        if headers:
            print("  Columns:")
            for header in headers:
                print(f"    {header.get('name', '?')}")
        print("\n  Rows:")
        for row in values[:20]:
            xvals = [entry.get("value") for entry in row.get("x", [])]
            yvals = [entry.get("value") for entry in row.get("y", [])]
            print(f"    x={xvals} y={yvals}")


def cmd_download(args):
    """Download all YAML tables for a record."""
    from urllib.request import urlretrieve

    outdir = Path(args.output_dir) / f"ins{args.inspire_id}"
    outdir.mkdir(parents=True, exist_ok=True)

    url = f"https://www.hepdata.net/download/submission/ins{args.inspire_id}/1/yaml"
    print(f"Downloading ins{args.inspire_id} to {outdir}...")

    tarball_path = outdir / "submission.tar.gz"
    urlretrieve(url, str(tarball_path))

    with tarfile.open(tarball_path, "r:gz") as tar:
        tar.extractall(outdir)
    os.remove(tarball_path)

    files = list(outdir.glob("*.yaml"))
    print(f"  Extracted {len(files)} YAML files")
    for f in sorted(files):
        print(f"    {f.name}")


def main():
    parser = argparse.ArgumentParser(prog="hepdata", description="CLI for HEPData (hepdata.net)")
    sub = parser.add_subparsers(dest="cmd")
    sub.required = True

    p = sub.add_parser("find", help="Search for a record")
    p.add_argument("query", help="arXiv ID, INSPIRE ID, or keywords")
    p.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("tables", help="List tables in a record")
    p.add_argument("inspire_id", type=int, help="INSPIRE record ID")
    p.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    p.set_defaults(func=cmd_tables)

    p = sub.add_parser("get", help="Fetch a table's data")
    p.add_argument("inspire_id", type=int)
    p.add_argument("table_name", help='Table name, e.g. "Table 1"')
    p.add_argument("--json", action="store_true", help="Print full JSON")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("download", help="Download all YAML files")
    p.add_argument("inspire_id", type=int)
    p.add_argument("--output-dir", default="HEPData")
    p.set_defaults(func=cmd_download)

    args = parser.parse_args()
    if getattr(args, "cmd", None) is None:
        parser.print_help()
        sys.exit(2)
    args.func(args)


if __name__ == "__main__":
    main()
