# `feynrules` — browse and download UFO models from the FeynRules database

**Purpose.** If the bundled MadGraph UFO models (`sm`, `MSSM_SLHA2`,
`SMEFTsim`) don't cover your paper's BSM scenario, fetch an additional
model from the FeynRules wiki.

**When to use.** Only when `bin/simulate info` doesn't already list a
suitable UFO. For standard SUSY / SMEFT / top-EFT the bundled models are
usually enough.

## Invocation

```bash
bin/feynrules categories                              # 7 top-level categories
bin/feynrules list --category SusyModels              # browse one category
bin/feynrules list --search "vector-like quark"       # substring search over slug/title/description
bin/feynrules info <model-slug>                       # show all attachments for a model
bin/feynrules fetch <model-slug> [--file <name>] [--all] [--dest <dir>] [--extract]
bin/feynrules refresh-catalog                         # re-scrape the wiki (catalog is cached)
```

Every subcommand takes `--json` for machine-readable output.

## Output

- `categories`: 7 top-level groupings (SusyModels, NLOModels, SMEFT, ...).
- `list`: `[{slug, title, description, category}]` matching the filter.
- `info`: one model's page + attachment list.
- `fetch`: downloads one or more attachments; `--extract` unpacks
  tarballs/zips. UFO-format tarballs are preferred by default when the
  page has them.

## Gotchas

- The local catalog at `LHCRecastBench/data/feynrules_catalog.json` is
  pre-built; browsing never touches the network. Only `fetch` and
  `refresh-catalog` do.
- Model slugs are the wiki-page slugs, not friendly names. Use
  `list --search` to find a slug.
- `fetch` can land large tarballs (some UFOs are 100+ MB). Use
  `--file <name>` to grab just what you need.

## Examples

```bash
# Find all SUSY-related models
bin/feynrules list --category SusyModels --json

# Search across everything
bin/feynrules list --search "leptoquark"

# Show what's attached to the MSSM page
bin/feynrules info MSSM

# Pull the UFO for a specific model and unpack into the workspace
bin/feynrules fetch LeptoquarkModel --extract --dest sim/models
```
