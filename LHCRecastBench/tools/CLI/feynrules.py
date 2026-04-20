#!/usr/bin/env python3
"""
CLI for the FeynRules model database (feynrules.irmp.ucl.ac.be).

Usage:
    feynrules categories
    feynrules list [--category SusyModels] [--search keyword]
    feynrules info <model>
    feynrules fetch <model> [--file <attachment>] [--dest DIR]
    feynrules refresh-catalog
"""

import argparse
import json
import re
import ssl
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen, Request, urlretrieve

try:
    ssl._create_default_https_context = ssl._create_unverified_context
except Exception:
    pass

BASE_URL = "https://feynrules.irmp.ucl.ac.be"

CATEGORIES = [
    ("StandardModel", "Standard Model"),
    ("SimpleExtensions", "Simple Extensions of the SM"),
    ("SusyModels", "Supersymmetric Models"),
    ("ExtraDimModels", "Extra-dimensional Models"),
    ("EffectiveModels", "Strongly Coupled and Effective Field Theories"),
    ("MiscellaneousModels", "Miscellaneous"),
    ("NLOModels", "NLO"),
]

REPO_ROOT = Path(__file__).resolve().parents[3]
CATALOG_PATH = REPO_ROOT / "LHCRecastBench" / "data" / "feynrules_catalog.json"

UFO_EXTS = (".tgz", ".tar.gz", ".zip", ".tar.bz2")

# ---------- HTTP ----------


def _http_get(url, retries=3, backoff=4):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = Request(url, headers={"User-Agent": "feynrules-tool/1.0"})
    for attempt in range(retries):
        try:
            with urlopen(req, timeout=30, context=ctx) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception:
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
            else:
                raise


# ---------- HTML parsing ----------


class _LinkExtractor(HTMLParser):
    """Collect (href, link_text) pairs."""

    def __init__(self):
        super().__init__()
        self.links = []
        self._in_a = False
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            attrs_d = dict(attrs)
            self._in_a = True
            self._href = attrs_d.get("href", "")
            self._text = []

    def handle_endtag(self, tag):
        if tag == "a" and self._in_a:
            self.links.append((self._href, "".join(self._text).strip()))
            self._in_a = False
            self._href = None
            self._text = []

    def handle_data(self, data):
        if self._in_a:
            self._text.append(data)


def _extract_links(html):
    p = _LinkExtractor()
    p.feed(html)
    return p.links


def _wiki_models_in_category(category_slug):
    """Find /wiki/<ModelName> links on a category page (excluding the category itself)."""
    html = _http_get(f"{BASE_URL}/wiki/{category_slug}")
    seen = set()
    models = []
    for href, text in _extract_links(html):
        if not href.startswith("/wiki/"):
            continue
        slug = href[len("/wiki/") :].split("#", 1)[0].split("?", 1)[0].strip("/")
        if not slug or slug == category_slug:
            continue
        # Skip TracWiki nav pages
        if slug in {
            "WikiStart",
            "TracGuide",
            "TitleIndex",
            "RecentChanges",
            "WikiFormatting",
            "InterMapTxt",
            "InterTrac",
            "TracBrowser",
            "TracQuery",
            "TracReports",
            "TracTickets",
            "TracTimeline",
            "TracWiki",
            "TracChangeset",
            "TracIni",
            "TracSearch",
            "ModelDatabaseMainPage",
            "WikiNewPage",
            "TracLogging",
        }:
            continue
        if slug in seen:
            continue
        seen.add(slug)
        models.append({"slug": slug, "title": text or slug})
    return models


def _attachments_in_model(model_slug):
    """Find /attachment/wiki/<ModelSlug>/<file> links on a model page."""
    try:
        html = _http_get(f"{BASE_URL}/wiki/{model_slug}")
    except Exception as exc:
        return [], None, str(exc)

    seen = set()
    attachments = []
    prefix = f"/attachment/wiki/{model_slug}/"
    for href, _text in _extract_links(html):
        # Trac uses /attachment/wiki/.../file (description page) and /raw-attachment/wiki/.../file (direct)
        if prefix in href or f"/raw-attachment/wiki/{model_slug}/" in href:
            # Normalise to /raw-attachment for downloads
            fname = href.rsplit("/", 1)[-1].split("?", 1)[0]
            if not fname:
                continue
            if fname in seen:
                continue
            seen.add(fname)
            raw_url = f"{BASE_URL}/raw-attachment/wiki/{model_slug}/{quote(fname)}"
            attachments.append({"filename": fname, "url": raw_url})

    # Try to extract a one-line description (first <p> text).
    desc = _first_paragraph(html)
    return attachments, desc, None


class _ParaCollector(HTMLParser):
    """Collect every <p> block as plain text."""

    def __init__(self):
        super().__init__()
        self.paragraphs = []
        self._in_p = False
        self._depth = 0
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag == "p":
            if not self._in_p:
                self._in_p = True
                self._depth = 1
                self._buf = []
            else:
                self._depth += 1

    def handle_endtag(self, tag):
        if tag == "p" and self._in_p:
            self._depth -= 1
            if self._depth <= 0:
                txt = re.sub(r"\s+", " ", "".join(self._buf)).strip()
                if txt:
                    self.paragraphs.append(txt)
                self._in_p = False
                self._buf = []

    def handle_data(self, data):
        if self._in_p:
            self._buf.append(data)


def _first_paragraph(html):
    """Return the first 'descriptive' paragraph: skip author/contact blurbs."""
    p = _ParaCollector()
    try:
        p.feed(html)
    except Exception:
        return ""
    for txt in p.paragraphs:
        # Skip author/contact paragraphs (emails, very short bylines).
        if "@" in txt:
            continue
        if len(txt) < 60:
            continue
        return txt[:400]
    # Fall back to the first non-empty paragraph if nothing substantial found.
    return p.paragraphs[0][:400] if p.paragraphs else ""


# ---------- Catalog ----------


def build_catalog(verbose=True):
    """Walk every category and model page, return a manifest dict."""
    catalog = {
        "source": BASE_URL,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "categories": [],
    }
    for slug, name in CATEGORIES:
        if verbose:
            print(f"[{slug}] scanning category...", file=sys.stderr)
        try:
            models = _wiki_models_in_category(slug)
        except Exception as exc:
            print(f"  ! failed to fetch category {slug}: {exc}", file=sys.stderr)
            continue
        cat_entry = {"slug": slug, "name": name, "models": []}
        for m in models:
            if verbose:
                print(f"  [{m['slug']}] fetching attachments...", file=sys.stderr)
            attachments, desc, err = _attachments_in_model(m["slug"])
            entry = {
                "slug": m["slug"],
                "title": m["title"],
                "description": desc or "",
                "wiki_url": f"{BASE_URL}/wiki/{m['slug']}",
                "attachments": attachments,
            }
            if err:
                entry["error"] = err
            cat_entry["models"].append(entry)
        catalog["categories"].append(cat_entry)
    return catalog


def load_catalog():
    if not CATALOG_PATH.exists():
        print(
            f"Catalog not found at {CATALOG_PATH}.\n" "Run: feynrules refresh-catalog",
            file=sys.stderr,
        )
        sys.exit(2)
    return json.loads(CATALOG_PATH.read_text())


def save_catalog(catalog):
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.write_text(json.dumps(catalog, indent=2))


def _iter_models(catalog, category=None):
    for cat in catalog["categories"]:
        if category and cat["slug"].lower() != category.lower():
            continue
        for m in cat["models"]:
            yield cat, m


def _find_model(catalog, name):
    """Match by exact slug (case-insensitive) or unique title prefix."""
    name_l = name.lower()
    exact = []
    partial = []
    for cat, m in _iter_models(catalog):
        if m["slug"].lower() == name_l:
            exact.append((cat, m))
        elif name_l in m["slug"].lower() or name_l in m["title"].lower():
            partial.append((cat, m))
    if exact:
        return exact[0]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        names = ", ".join(f"{m['slug']} ({c['slug']})" for c, m in partial[:8])
        print(f"Ambiguous model '{name}'. Candidates: {names}", file=sys.stderr)
        sys.exit(2)
    print(f"Model '{name}' not found in catalog.", file=sys.stderr)
    sys.exit(2)


# ---------- Subcommands ----------


def cmd_categories(args):
    if args.json:
        print(json.dumps([{"slug": s, "name": n} for s, n in CATEGORIES], indent=2))
        return
    print("FeynRules model categories:\n")
    for s, n in CATEGORIES:
        print(f"  {s:<22} {n}")


def cmd_list(args):
    catalog = load_catalog()
    rows = []
    for cat, m in _iter_models(catalog, category=args.category):
        if args.search:
            blob = f"{m['slug']} {m['title']} {m['description']}".lower()
            if args.search.lower() not in blob:
                continue
        rows.append(
            {
                "category": cat["slug"],
                "slug": m["slug"],
                "title": m["title"],
                "description": m["description"],
                "n_attachments": len(m.get("attachments", [])),
            }
        )

    if args.json:
        print(json.dumps(rows, indent=2))
        return

    if not rows:
        print("(no models match)")
        return
    print(f"{len(rows)} model(s):\n")
    for r in rows:
        line = f"  [{r['category']}] {r['slug']}  ({r['n_attachments']} attachments)"
        print(line)
        if r["title"] and r["title"] != r["slug"]:
            print(f"      {r['title']}")
        if r["description"]:
            print(f"      {r['description'][:120]}")


def cmd_info(args):
    catalog = load_catalog()
    cat, m = _find_model(catalog, args.model)
    if args.json:
        out = dict(m)
        out["category"] = cat["slug"]
        print(json.dumps(out, indent=2))
        return
    print(f"Model: {m['slug']}  [{cat['slug']}]")
    print(f"  Title: {m['title']}")
    if m.get("description"):
        print(f"  Description: {m['description']}")
    print(f"  Wiki: {m['wiki_url']}")
    atts = m.get("attachments", [])
    print(f"  Attachments ({len(atts)}):")
    for a in atts:
        marker = "  *" if a["filename"].lower().endswith(UFO_EXTS) else "   "
        print(f"   {marker} {a['filename']}")


def cmd_fetch(args):
    catalog = load_catalog()
    cat, m = _find_model(catalog, args.model)
    atts = m.get("attachments", [])
    if not atts:
        print(f"No attachments listed for {m['slug']}.", file=sys.stderr)
        sys.exit(2)

    # Decide which file(s) to download.
    if args.file:
        chosen = [a for a in atts if a["filename"] == args.file]
        if not chosen:
            print(f"No attachment named '{args.file}'. Available:", file=sys.stderr)
            for a in atts:
                print(f"  {a['filename']}", file=sys.stderr)
            sys.exit(2)
    elif args.all:
        chosen = atts
    else:
        # Default: prefer files that look like UFO tarballs.
        archives = [a for a in atts if a["filename"].lower().endswith(UFO_EXTS)]
        ufo_like = [a for a in archives if "ufo" in a["filename"].lower()]
        chosen = ufo_like or archives
        if not chosen:
            print(
                f"No archive attachments for {m['slug']}. Use --file <name> or --all.",
                file=sys.stderr,
            )
            for a in atts:
                print(f"  {a['filename']}", file=sys.stderr)
            sys.exit(2)

    dest = Path(args.dest) / m["slug"]
    dest.mkdir(parents=True, exist_ok=True)

    downloaded = []
    for a in chosen:
        out = dest / a["filename"]
        print(f"  fetching {a['filename']} -> {out}", file=sys.stderr)
        try:
            urlretrieve(a["url"], str(out))
        except Exception as exc:
            print(f"  ! download failed: {exc}", file=sys.stderr)
            continue
        downloaded.append(str(out))

        if args.extract and a["filename"].lower().endswith((".tgz", ".tar.gz", ".tar.bz2")):
            import tarfile

            try:
                with tarfile.open(out, "r:*") as tar:
                    tar.extractall(dest)
                print(f"    extracted into {dest}", file=sys.stderr)
            except Exception as exc:
                print(f"    ! extract failed: {exc}", file=sys.stderr)
        elif args.extract and a["filename"].lower().endswith(".zip"):
            import zipfile

            try:
                with zipfile.ZipFile(out) as zf:
                    zf.extractall(dest)
                print(f"    extracted into {dest}", file=sys.stderr)
            except Exception as exc:
                print(f"    ! extract failed: {exc}", file=sys.stderr)

    if args.json:
        print(
            json.dumps(
                {
                    "model": m["slug"],
                    "category": cat["slug"],
                    "dest": str(dest),
                    "downloaded": downloaded,
                },
                indent=2,
            )
        )
    else:
        print(f"\nDownloaded {len(downloaded)} file(s) to {dest}")


def cmd_refresh(args):
    catalog = build_catalog(verbose=not args.quiet)
    save_catalog(catalog)
    n_models = sum(len(c["models"]) for c in catalog["categories"])
    n_attach = sum(len(m["attachments"]) for c in catalog["categories"] for m in c["models"])
    print(f"\nWrote {CATALOG_PATH}", file=sys.stderr)
    print(f"  categories: {len(catalog['categories'])}", file=sys.stderr)
    print(f"  models:     {n_models}", file=sys.stderr)
    print(f"  attachments:{n_attach}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        prog="feynrules",
        description="Browse and download UFO models from the FeynRules database.",
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.required = True

    p = sub.add_parser("categories", help="List the top-level model categories.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_categories)

    p = sub.add_parser("list", help="List models in the catalog.")
    p.add_argument("--category", help="Filter by category slug (e.g. SusyModels, NLOModels).")
    p.add_argument("--search", help="Substring filter on slug/title/description.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("info", help="Show details and attachments for one model.")
    p.add_argument("model", help="Model slug or partial name.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("fetch", help="Download model attachment(s).")
    p.add_argument("model", help="Model slug or partial name.")
    p.add_argument("--file", help="Specific attachment filename to download.")
    p.add_argument("--all", action="store_true", help="Download every attachment.")
    p.add_argument("--dest", default="feynrules_models", help="Destination root directory.")
    p.add_argument("--extract", action="store_true", help="Extract tarballs/zips after download.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("refresh-catalog", help="Rebuild the local catalog by scraping the wiki.")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_refresh)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
