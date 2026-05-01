#!/usr/bin/env python3
"""Read a paper PDF: extract text, render figures, or list sections.

Usage:
    read-paper <paper.pdf>                        # full text
    read-paper <paper.pdf> --pages 3-5            # specific pages
    read-paper <paper.pdf> --figures              # extract figures as PNG to papers/figures/
    read-paper <paper.pdf> --figures --pages 8-10 # figures from specific pages only
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _open_doc(pdf_path: str):
    try:
        import fitz
    except ImportError:
        print("ERROR: pymupdf not installed. Run: pip install pymupdf", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(pdf_path):
        print(f"ERROR: file not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)
    return fitz.open(pdf_path)


def _parse_page_range(pages_str: str, total: int) -> list[int]:
    """Parse '3-5' or '3' or '1,4-6' into a list of 0-indexed page numbers."""
    result = []
    for part in pages_str.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            a, b = int(a), int(b)
            result.extend(range(a - 1, min(b, total)))
        else:
            idx = int(part) - 1
            if 0 <= idx < total:
                result.append(idx)
    return sorted(set(result))


def cmd_text(pdf_path: str, pages: list[int] | None):
    doc = _open_doc(pdf_path)
    total = len(doc)
    if pages is None:
        pages = list(range(total))

    print(f"[{Path(pdf_path).name}: {total} pages, showing {len(pages)}]\n")
    for i in pages:
        page = doc[i]
        text = page.get_text()
        print(f"{'=' * 60}")
        print(f"PAGE {i + 1}")
        print(f"{'=' * 60}")
        print(text)
    doc.close()


def cmd_figures(pdf_path: str, pages: list[int] | None, output_dir: str | None):
    import fitz

    doc = _open_doc(pdf_path)
    total = len(doc)
    if pages is None:
        pages = list(range(total))

    if output_dir is None:
        output_dir = str(Path(pdf_path).parent / "figures")
    os.makedirs(output_dir, exist_ok=True)

    rendered = []
    for i in pages:
        page = doc[i]
        text = page.get_text()
        # Render pages that mention figures, or all requested pages
        has_figure = "Figure" in text or "Fig." in text or "fig." in text
        if has_figure or len(pages) <= 10:
            mat = fitz.Matrix(3.0, 3.0)  # 3x zoom for readable figures
            pix = page.get_pixmap(matrix=mat)
            fname = f"page{i + 1:02d}.png"
            fpath = os.path.join(output_dir, fname)
            pix.save(fpath)
            rendered.append((i + 1, fpath, has_figure))

    doc.close()

    if rendered:
        print(f"Rendered {len(rendered)} pages to {output_dir}/:")
        for page_num, fpath, has_fig in rendered:
            marker = " [has figure]" if has_fig else ""
            print(f"  page {page_num:2d}: {Path(fpath).name}{marker}")
    else:
        print("No figure pages found in the requested range.")


def main():
    parser = argparse.ArgumentParser(
        description="Read a paper PDF: extract text or render figures.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("pdf", help="Path to the PDF file")
    parser.add_argument(
        "--pages", default=None, help="Page range, e.g. '3-5' or '1,4-6' (1-indexed)"
    )
    parser.add_argument(
        "--figures",
        action="store_true",
        help="Render pages as PNG images instead of extracting text",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for figures (default: <pdf_dir>/figures/)",
    )
    args = parser.parse_args()

    doc = _open_doc(args.pdf)
    total = len(doc)
    doc.close()

    pages = _parse_page_range(args.pages, total) if args.pages else None

    if args.figures:
        cmd_figures(args.pdf, pages, args.output_dir)
    else:
        cmd_text(args.pdf, pages)


if __name__ == "__main__":
    main()
