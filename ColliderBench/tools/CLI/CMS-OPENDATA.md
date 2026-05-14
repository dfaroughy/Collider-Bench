# `cms-opendata` — browse and stream the CMS Open Data portal

**Purpose.** Search for released CMS datasets (data + MC), list the ROOT
files behind a record, stream NanoAOD branches remotely without
downloading the whole file, and resolve NanoAOD → parent MiniAOD to pick
up cross sections / generator cards.

**When to use.** To get the data + background MC for your recast, and to
look up σ (used with luminosity to set normalizations).

## Invocation

```bash
bin/cms-opendata search <query> [--experiment CMS] [--subtype ...] [--energy 13TeV] --json
bin/cms-opendata record <recid> --json                           # one record's metadata
bin/cms-opendata files  <recid> [--urls] [--limit N] [--save <f>]
bin/cms-opendata stream <root://url> --branches <b1> <b2> ...    # remote-stream branches
bin/cms-opendata sample-info <nano-recid>                        # follow NanoAOD → parent MiniAOD
bin/cms-opendata sample-info <nano-recid> --json
```

## Output

- `search`: list of matching records with titles + recids + subtype +
  energy.
- `record`: full metadata for one record.
- `files`: file list (ROOT files behind the record). With `--urls`,
  includes the xrootd URLs you can stream directly.
- `stream`: prints a few events worth of the requested branches to let
  you inspect structure before committing to a full analysis run.
- `sample-info`: cross section, generator card link, and MC settings
  pulled from the parent MiniAOD record.

All commands support `--json` for machine-readable output.

## Gotchas

- **Prefer `root://` (xrootd) URLs over `https://`** for performance and
  reliability. HTTPS works on the public internet but is single-stream and
  noticeably slower for large NanoAOD files; on isolated/federated networks
  (NERSC and similar) it can also fail outright with SSL errors. xrootd
  uses parallel streams + CERN's federated peering, which is what the
  underlying CMS Open Data tooling expects.
- For cross sections, **pass the NanoAOD recid to `sample-info`**, not
  the MiniAOD recid — the tool walks the provenance chain for you.
- `files --limit` defaults to 20; use `--limit 100000` or `--save
  <file>` to dump the full list.
- Streaming doesn't cache — repeated calls re-read from CMS servers.
  For production, prefer our `tools.streaming.stream_files` Python
  helper which handles parallelism + caching.

## Examples

```bash
# Find Run 2 diboson samples
bin/cms-opendata search "ZZTo4L" --energy 13TeV --json | head -40

# List the files for one record (just the xrootd URLs)
bin/cms-opendata files 19983 --urls --limit 50

# Remote-peek a NanoAOD file to see which branches are available
bin/cms-opendata stream root://eospublic.cern.ch//eos/.../file.root \
    --branches Muon_pt Muon_eta --max-events 100

# Pull σ and MG5 card location for a NanoAOD sample
bin/cms-opendata sample-info 19983 --json
```
