# `read-paper` — extract text and figures from a paper PDF

**Purpose.** Pull the text (all or specific pages) out of a paper and render
figure pages as PNGs the agent can actually look at.

**When to use.** At the start of any recast: read the paper to find the
selection, the observable binning, the luminosity, the cross sections
cited, and the model benchmark points. Use `--figures` to inspect the
published histograms you're trying to reproduce.

## Invocation

```bash
bin/read-paper <paper.pdf>                        # full text to stdout
bin/read-paper <paper.pdf> --pages 3-5            # specific pages
bin/read-paper <paper.pdf> --pages 1,4-6          # ranges + singletons, comma-separated
bin/read-paper <paper.pdf> --figures              # render figure pages as PNG
bin/read-paper <paper.pdf> --figures --pages 8-10 # figures from specific pages only
```

## Output

- Text mode: extracted text on stdout.
- Figure mode: PNGs written to `papers/figures/<paper_stem>_page_NNN.png`
  (one per page). Read the PNGs with the `Read` tool to inspect them
  visually.

## Gotchas

- Figure mode renders the **whole page**, not individual subfigures. You
  get a page bitmap, which is fine for inspection but not for parsing.
- If the PDF uses unusual encodings (scanned text, ligature-heavy
  typesetting) extraction may produce garbled output — fall back to
  `--figures` and read visually.

## Examples

```bash
# Skim the analysis section of a CMS SUSY paper
bin/read-paper papers/CMS-SUS-16-047.pdf --pages 6-12

# Get Figure 4 (search it by page from the TOC first, then render)
bin/read-paper papers/CMS-SUS-16-047.pdf --figures --pages 15

# Extract all tables cited in the paper's body
bin/read-paper papers/CMS-SUS-16-047.pdf | grep -A 8 "^Table"
```
