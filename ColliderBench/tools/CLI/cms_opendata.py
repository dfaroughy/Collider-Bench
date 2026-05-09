#!/usr/bin/env python3
"""
CLI for CMS Open Data portal operations.

Usage:
    cms-opendata search <sample> --energy 13TeV
    cms-opendata record <recid>
    cms-opendata files 75589 --limit 50
    cms-opendata stream <url> --branches Muon_pt Muon_eta --max-events 5000
    cms-opendata download <url1> <url2> --output-dir ./data
    cms-opendata sample-info <recid>
"""

import argparse
import hashlib
import json
import re
import shutil
import ssl
import sys
from pathlib import Path
from urllib.request import urlopen, Request, build_opener, HTTPSHandler

API_BASE = "https://opendata.cern.ch/api/records/"
XROOTD_PREFIX = "root://eospublic.cern.ch/"
HTTP_PREFIX = "https://eospublic.cern.ch/"

_DATASET_NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9]*([-_][A-Za-z0-9]+)+$")


def _print_json(payload):
    try:
        print(json.dumps(payload, indent=2, sort_keys=True))
    except BrokenPipeError:
        pass


def _smart_query(query):
    query = query.strip()
    if not query or ":" in query:
        return query
    tokens = query.split()
    if len(tokens) == 1:
        return f"title:*{query}*"
    return " ".join(f"title:*{t}*" if _DATASET_NAME_RE.match(t) else t for t in tokens)


def _api_get(url, params=None):
    if params:
        from urllib.parse import urlencode

        url = url + "?" + urlencode(params)
    req = Request(url)
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _xrootd_to_http(uri):
    if uri.startswith(XROOTD_PREFIX):
        return HTTP_PREFIX + uri[len(XROOTD_PREFIX) :].lstrip("/")
    return uri


def _extract_files(metadata):
    results = []
    for idx in metadata.get("_file_indices", []):
        for f in idx.get("files", []):
            uri = f.get("uri", "")
            entry = {
                "filename": f.get("filename", f.get("key", "")),
                "size": f.get("size"),
                "xrootd_uri": uri,
                "http_url": _xrootd_to_http(uri),
            }
            checksum = f.get("checksum")
            if checksum:
                entry["checksum"] = checksum
            results.append(entry)
    if not results:
        for f in metadata.get("_files", metadata.get("files", [])):
            uri = f.get("uri", "")
            entry = {"filename": f.get("key", ""), "size": f.get("size")}
            if uri:
                entry["xrootd_uri"] = uri
                entry["http_url"] = _xrootd_to_http(uri)
            checksum = f.get("checksum")
            if checksum:
                entry["checksum"] = checksum
            results.append(entry)
    return results


def _cached_download_path(url):
    cache_dir = Path.home() / ".cache" / "cms_opendata"
    cache_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(url.split("?")[0]).suffix or ".root"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"{digest}{suffix}"


def _download_with_relaxed_ssl(url, dest):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    opener = build_opener(HTTPSHandler(context=ctx))
    with opener.open(url, timeout=120) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)


def _exception_chain(exc):
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        yield current
        seen.add(id(current))
        current = current.__cause__ or current.__context__


def _is_ssl_certificate_failure(exc):
    markers = (
        "certificate verify failed",
        "clientconnectorcertificateerror",
        "sslcertverificationerror",
        "self-signed certificate",
    )
    for item in _exception_chain(exc):
        message = f"{type(item).__name__}: {item}".lower()
        if any(marker in message for marker in markers):
            return True
    return False


def _open_events_tree(url, timeout=120):
    import uproot

    try:
        return uproot.open(url + ":Events", timeout=timeout)
    except Exception as exc:
        if not url.startswith(("http://", "https://")):
            raise
        if not _is_ssl_certificate_failure(exc):
            raise
        cached_path = _cached_download_path(url)
        if not cached_path.exists():
            print(
                "Direct remote streaming failed; downloading a local cached copy for inspection.",
                file=sys.stderr,
            )
            _download_with_relaxed_ssl(url, str(cached_path))
        return uproot.open(str(cached_path) + ":Events", timeout=timeout)


# =====================================================================
# Commands
# =====================================================================


def cmd_search(args):
    """Search for datasets."""
    params = {"page": args.page, "size": args.size}
    if args.query:
        params["q"] = _smart_query(args.query)
    if args.experiment:
        params["experiment"] = args.experiment
    params["type"] = "Dataset"
    if args.subtype:
        params["subtype"] = args.subtype
    if args.energy:
        params["collision_energy"] = args.energy

    data = _api_get(API_BASE, params)
    hits = data.get("hits", {})
    total = hits.get("total", 0)
    rows = []

    for h in hits.get("hits", []):
        meta = h.get("metadata", {})
        dist = meta.get("distribution", {})
        rows.append(
            {
                "recid": meta.get("recid", ""),
                "title": meta.get("title", ""),
                "title_additional": meta.get("title_additional", ""),
                "collision_energy": meta.get("collision_information", {}).get("energy", ""),
                "events": dist.get("number_events"),
                "files": dist.get("number_files"),
                "doi": meta.get("doi", ""),
                "license": meta.get("license", {}),
                "raw_metadata": meta if args.json else None,
            }
        )

    if args.json:
        _print_json(
            {
                "query": params.get("q", ""),
                "page": args.page,
                "size": args.size,
                "total": total,
                "results": rows,
            }
        )
        return

    print(f"Found {total} datasets (showing page {args.page}):\n")
    for row in rows:
        recid = row["recid"]
        title = row["title"][:100]
        nevt = row["events"] if row["events"] is not None else "?"
        nfiles = row["files"] if row["files"] is not None else "?"
        title_add = row.get("title_additional", "")
        energy = row.get("collision_energy", "")
        print(f"  [{recid}] {title}")
        if title_add:
            print(f"         {title_add[:100]}")
        extra = f"events={nevt}, files={nfiles}"
        if energy:
            extra += f", energy={energy}"
        print(f"         {extra}")


def cmd_record(args):
    """Fetch record metadata."""
    data = _api_get(f"{API_BASE}{args.recid}")
    meta = data.get("metadata", {})
    dist = meta.get("distribution", {})

    if args.json:
        collision_info = meta.get("collision_information", {})
        summary = {
            "recid": meta.get("recid"),
            "title": meta.get("title", ""),
            "title_additional": meta.get("title_additional", ""),
            "abstract": meta.get("abstract", {}).get("description", ""),
            "collision_info": collision_info,
            "date_created": meta.get("date_created", ""),
            "run_period": meta.get("run_period", []),
            "doi": meta.get("doi", ""),
            "events": dist.get("number_events"),
            "files": dist.get("number_files"),
            "methodology": meta.get("methodology", {}),
            "usage": meta.get("usage", {}),
            "validation": meta.get("validation", {}),
            "generation": meta.get("generation", {}),
            "keywords": meta.get("keywords", []),
            "relations": meta.get("relations", []),
            "license": meta.get("license", {}),
            "num_file_indices": len(meta.get("_file_indices", [])),
            "raw_metadata": meta,
        }
        _print_json(summary)
        return

    print(f"Record {args.recid}")
    print(f"  Title: {meta.get('title', '')}")
    title_add = meta.get("title_additional", "")
    if title_add:
        print(f"  Additional: {title_add}")
    abstract = meta.get("abstract", {}).get("description", "")
    if abstract:
        print(f"  Abstract: {abstract[:200]}{'...' if len(abstract) > 200 else ''}")
    collision_info = meta.get("collision_information", {})
    if collision_info:
        print(
            f"  Collision: {collision_info.get('type', '')} at {collision_info.get('energy', '')}"
        )
    date_created = meta.get("date_created", "")
    if date_created:
        print(f"  Date created: {date_created}")
    run_period = meta.get("run_period", [])
    if run_period:
        print(
            f"  Run period: {', '.join(run_period) if isinstance(run_period, list) else run_period}"
        )
    print(f"  Events: {dist.get('number_events', '?')}")
    print(f"  Files: {dist.get('number_files', '?')}")
    print(f"  DOI: {meta.get('doi', '')}")
    methodology = meta.get("methodology", {})
    if methodology:
        desc = methodology.get("description", "")
        if desc:
            print(f"  Methodology: {desc[:150]}{'...' if len(desc) > 150 else ''}")
    usage = meta.get("usage", {})
    if usage:
        desc = usage.get("description", "")
        if desc:
            print(f"  Usage: {desc[:150]}{'...' if len(desc) > 150 else ''}")
    validation = meta.get("validation", {})
    if validation:
        desc = validation.get("description", "")
        if desc:
            print(f"  Validation: {desc[:150]}{'...' if len(desc) > 150 else ''}")
    generation = meta.get("generation", {})
    if generation:
        gen_steps = generation.get("steps", [])
        if gen_steps:
            print(f"  Generation steps: {len(gen_steps)}")
    keywords = meta.get("keywords", [])
    if keywords:
        kw_strs = [f"{kw.get('name', '')}: {kw.get('value', '')}" for kw in keywords[:5]]
        print(f"  Keywords: {'; '.join(kw_strs)}")
    relations = meta.get("relations", [])
    if relations:
        print(f"  Relations: {len(relations)}")
        for rel in relations[:3]:
            print(f"    {rel.get('type', '')}: {rel.get('title', rel.get('recid', ''))}")
    num_idx = len(meta.get("_file_indices", []))
    if num_idx:
        print(f"  File indices: {num_idx}")


def cmd_files(args):
    """List files for a record."""
    data = _api_get(f"{API_BASE}{args.recid}")
    meta = data.get("metadata", {})
    files = _extract_files(meta)

    # Gather file index summaries
    file_indices = meta.get("_file_indices", [])
    index_summaries = []
    for i, idx in enumerate(file_indices):
        idx_files = idx.get("files", [])
        total_size = sum(f.get("size", 0) for f in idx_files)
        index_summaries.append(
            {
                "index": i,
                "key": idx.get("key", ""),
                "num_files": len(idx_files),
                "total_size": total_size,
            }
        )

    offset = args.offset
    limit = args.limit
    page = files[offset : offset + limit]

    if args.json:
        _print_json(
            {
                "recid": args.recid,
                "total_files": len(files),
                "file_indices": index_summaries,
                "offset": offset,
                "limit": limit,
                "returned": len(page),
                "files": page,
            }
        )
        return

    if index_summaries:
        print(f"Record {args.recid}: {len(file_indices)} file index group(s)\n")
        for si in index_summaries:
            size_mb = si["total_size"] / 1e6 if si["total_size"] else 0
            print(
                f"  Index {si['index']}: key={si['key']}, files={si['num_files']}, size={size_mb:.1f} MB"
            )
        print()

    print(
        f"Record {args.recid}: {len(files)} files total (showing {offset}-{offset + len(page)})\n"
    )
    for f in page:
        size_mb = f.get("size", 0) / 1e6 if f.get("size") else 0
        print(f"  {f['filename']:<50} {size_mb:>8.1f} MB")
        if args.urls:
            print(f"    {f.get('xrootd_uri', f.get('http_url', ''))}")
        checksum = f.get("checksum")
        if checksum:
            print(f"    checksum: {checksum}")

    if args.save:
        urls = [
            f.get("xrootd_uri", f.get("http_url", ""))
            for f in files
            if f.get("xrootd_uri") or f.get("http_url")
        ]
        with open(args.save, "w") as out:
            out.write("\n".join(urls))
        print(f"\nSaved {len(urls)} URLs to {args.save}")


def cmd_stream(args):
    """Stream NanoAOD branches from a remote file."""
    import awkward as ak

    url = args.url
    if url.startswith("root://"):
        url = _xrootd_to_http(url)

    f = _open_events_tree(url, timeout=120)

    if not args.branches:
        # List branches
        filt = args.collection or ""
        branch_info = []
        for name in sorted(f.keys()):
            if filt and filt not in name:
                continue
            branch = f[name]
            branch_info.append(
                {
                    "name": name,
                    "typename": branch.typename,
                    "interpretation": str(branch.interpretation),
                }
            )

        if args.json:
            _print_json(
                {
                    "url": args.url,
                    "num_entries": f.num_entries,
                    "num_branches": len(f.keys()),
                    "branches": branch_info,
                }
            )
            return

        print(f"Tree 'Events': {f.num_entries} entries, {len(f.keys())} branches\n")
        for bi in branch_info:
            print(f"  {bi['name']}: {bi['typename']}")
    else:
        # Read data
        arrays = f.arrays(
            args.branches,
            entry_start=args.entry_start,
            entry_stop=args.entry_start + args.max_events,
            library="ak",
        )
        n = len(arrays)

        if args.json:
            # Return actual array data as JSON
            branch_data = {}
            for key in arrays.fields:
                col = arrays[key]
                branch_data[key] = ak.to_list(col)
            _print_json(
                {
                    "mode": "data",
                    "num_events": n,
                    "branches": branch_data,
                }
            )
            return

        # Plain text: show summary stats
        print(f"Read {n} events, branches: {args.branches}\n")
        for key in arrays.fields:
            col = arrays[key]
            flat = ak.flatten(col) if col.ndim > 1 else col
            if len(flat) > 0:
                import numpy as np

                print(
                    f"  {key}: n={len(flat)}, mean={np.mean(flat):.4f}, "
                    f"min={np.min(flat):.4f}, max={np.max(flat):.4f}"
                )


def cmd_download(args):
    """Download ROOT files."""
    import urllib.request

    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    for url in args.urls[: args.max_files]:
        if url.startswith("root://"):
            url = _xrootd_to_http(url)
        filename = url.split("/")[-1]
        dest = outdir / filename
        print(f"  Downloading {filename}...", end="", flush=True)
        urllib.request.urlretrieve(url, str(dest))
        size_mb = dest.stat().st_size / 1e6
        print(f" {size_mb:.1f} MB")

    print(f"\nDone. Files saved to {outdir}")


def cmd_sample_info(args):
    """Fetch full sample provenance: cross section, generator configs, production chain.

    Follows the chain: NanoAOD → MiniAOD → GEN-SIM, collecting cross sections
    and downloading generator configuration artifacts.
    """

    nano_recid = args.recid
    data = _api_get(f"{API_BASE}{nano_recid}")
    nano_meta = data.get("metadata", {})
    nano_title = nano_meta.get("title", "")

    verbose = not args.json

    def log(msg):
        if verbose:
            print(msg)

    log(f"Sample info for recid {nano_recid}")
    log(f"  Title: {nano_title}")

    # ── Find parent MiniAOD via relations ──
    mini_recid = None
    for rel in nano_meta.get("relations", []):
        desc = str(rel.get("description", ""))
        if "MINIAODSIM" in desc or "MINIAOD" in desc:
            mini_recid = rel.get("recid")
            break

    if mini_recid is None:
        log("  WARNING: No parent MiniAOD record found in relations.")
        if args.json:
            _print_json(
                {"nano_recid": nano_recid, "title": nano_title, "error": "No parent MiniAOD found"}
            )
        return

    log(f"  Parent MiniAOD: recid {mini_recid}")

    # ── Fetch MiniAOD record → cross section ──
    mini_data = _api_get(f"{API_BASE}{mini_recid}")
    mini_meta = mini_data.get("metadata", {})
    xsec = mini_meta.get("cross_section", {})

    if xsec:
        log("\n  Cross section:")
        log(
            f"    cross_section_pb:      {xsec.get('total_value', '?')}  ← USE THIS VALUE for normalization"
        )
        log(f"    uncertainty_pb:        {xsec.get('total_value_uncertainty', '?')}")
    else:
        log("\n  Cross section: not available on this record")

    # ── Walk the production chain (methodology.steps) ──
    methodology = mini_meta.get("methodology", {})
    steps = methodology.get("steps", [])
    gen_artifacts = []

    log(f"\n  Production chain ({len(steps)} steps):")
    for i, step in enumerate(steps):
        step_type = step.get("type", f"step_{i}")
        release = step.get("release", "?")
        generators = step.get("generators", [])
        gen_str = ", ".join(generators) if generators else ""
        log(
            f"    {i}: {step_type}  release={release}"
            + (f"  generators={gen_str}" if gen_str else "")
        )

        # Collect all configuration/parameter file URLs
        for cfg in step.get("configuration_files", []):
            # cfg can be a dict with 'url'/'title' or a string
            if isinstance(cfg, dict):
                url = cfg.get("url", "")
                title = cfg.get("title", cfg.get("description", ""))
            elif isinstance(cfg, str):
                # Try to extract URLs from script content
                url = ""
                title = "configuration_script"
            else:
                continue
            if url:
                gen_artifacts.append(
                    {
                        "step": step_type,
                        "step_index": i,
                        "title": title,
                        "url": url,
                    }
                )

        # Check for generator_parameters links
        for gp in step.get("generator_parameters", []):
            if isinstance(gp, dict):
                url = gp.get("url", "")
                title = gp.get("title", "generator_parameters")
            elif isinstance(gp, str):
                url = gp
                title = "generator_parameters"
            else:
                continue
            if url:
                gen_artifacts.append(
                    {
                        "step": step_type,
                        "step_index": i,
                        "title": title,
                        "url": url,
                    }
                )

    # ── Download and concatenate artifacts ──
    if gen_artifacts and not args.no_download:
        log(f"\n  Generator artifacts ({len(gen_artifacts)} files):")
        all_text = []
        for art in gen_artifacts:
            url = art["url"]
            # Make relative URLs absolute
            if url.startswith("/"):
                url = "https://opendata.cern.ch" + url
            log(f"    [{art['step']}] {art['title']}: {url}")
            try:
                content = _fetch_text(url)
                all_text.append(
                    f"{'=' * 70}\n"
                    f"ARTIFACT: {art['title']}\n"
                    f"STEP: {art['step']} (index {art['step_index']})\n"
                    f"URL: {url}\n"
                    f"{'=' * 70}\n"
                    f"{content}\n"
                )
            except Exception as e:
                all_text.append(
                    f"{'=' * 70}\nARTIFACT: {art['title']} — FAILED TO DOWNLOAD: {e}\n{'=' * 70}\n"
                )

        # Write concatenated artifacts
        if args.output:
            out_path = args.output
        else:
            out_path = f"sample_info_{nano_recid}.txt"
        with open(out_path, "w") as f:
            f.write(f"# Sample info for recid {nano_recid}\n")
            f.write(f"# Title: {nano_title}\n")
            f.write(f"# MiniAOD recid: {mini_recid}\n")
            if xsec:
                f.write(
                    f"# Cross section (pb): {xsec.get('total_value', '?')}  ← USE THIS for normalization\n"
                )
                f.write(f"# Uncertainty (pb): {xsec.get('total_value_uncertainty', '?')}\n")
            f.write("\n")
            f.write("\n".join(all_text))
        log(f"\n  Artifacts written to: {out_path}")
    elif gen_artifacts:
        log(f"\n  {len(gen_artifacts)} artifacts found (use without --no-download to fetch them)")

    # ── JSON output ──
    if args.json:
        result = {
            "nano_recid": nano_recid,
            "mini_recid": int(mini_recid),
            "title": nano_title,
            "cross_section_pb": float(xsec["total_value"]) if xsec.get("total_value") else None,
            "cross_section_uncertainty_pb": float(xsec["total_value_uncertainty"])
            if xsec.get("total_value_uncertainty")
            else None,
            "cross_section_note": "Use cross_section_pb directly. Matching and filter efficiencies are already included.",
            "cross_section_raw": xsec,
            "production_steps": [
                {
                    "type": s.get("type", f"step_{i}"),
                    "release": s.get("release", ""),
                    "generators": s.get("generators", []),
                }
                for i, s in enumerate(steps)
            ],
            "artifacts": gen_artifacts,
        }
        _print_json(result)


def _fetch_text(url):
    """Fetch a URL and return its text content."""
    import ssl
    from urllib.request import urlopen, Request

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = Request(url)
    with urlopen(req, timeout=30, context=ctx) as resp:
        raw = resp.read()
        # Try UTF-8, fall back to latin-1
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1")


# =====================================================================
# Main
# =====================================================================


def main():
    parser = argparse.ArgumentParser(
        prog="cms_opendata", description="CLI for CMS Open Data portal and Perlmutter analysis"
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.required = True

    # search
    p = sub.add_parser("search", help="Search for datasets")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--experiment", default="CMS")
    p.add_argument("--subtype", default="")
    p.add_argument("--energy", default="")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--size", type=int, default=10)
    p.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    p.set_defaults(func=cmd_search)

    # record
    p = sub.add_parser("record", help="Fetch record metadata")
    p.add_argument("recid", type=int)
    p.add_argument("--json", action="store_true", help="Print full JSON")
    p.set_defaults(func=cmd_record)

    # files
    p = sub.add_parser("files", help="List files for a record")
    p.add_argument("recid", type=int)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--urls", action="store_true", help="Show HTTP URLs")
    p.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    p.add_argument("--save", metavar="FILE", help="Save all URLs to file")
    p.set_defaults(func=cmd_files)

    # stream
    p = sub.add_parser("stream", help="Stream NanoAOD from remote file")
    p.add_argument("url", help="HTTP or XRootD URL")
    p.add_argument("--branches", nargs="*", default=[])
    p.add_argument("--collection", default="", help="Filter branch listing")
    p.add_argument("--max-events", type=int, default=1000)
    p.add_argument("--entry-start", type=int, default=0)
    p.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    p.set_defaults(func=cmd_stream)

    # download
    p = sub.add_parser("download", help="Download ROOT files")
    p.add_argument("urls", nargs="+", help="HTTP or XRootD URLs")
    p.add_argument("--output-dir", default="./cms_data")
    p.add_argument("--max-files", type=int, default=5)
    p.set_defaults(func=cmd_download)

    # sample-info
    p = sub.add_parser(
        "sample-info",
        help="Get cross section, generator configs, and production chain for a MC sample",
    )
    p.add_argument("recid", type=int, help="NanoAOD record ID")
    p.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    p.add_argument(
        "--no-download", action="store_true", help="Skip downloading generator artifacts"
    )
    p.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output file for artifacts (default: sample_info_<recid>.txt)",
    )
    p.set_defaults(func=cmd_sample_info)

    args = parser.parse_args()
    if getattr(args, "cmd", None) is None:
        parser.print_help()
        sys.exit(2)
    args.func(args)


if __name__ == "__main__":
    main()
