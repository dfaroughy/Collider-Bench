"""Parallel file streaming for CMS Open Data analysis.

Provides a simple interface to process ROOT files in parallel using uproot.
Auto-tunes workers and step_size based on observed throughput.

Usage:
    from ColliderBench.tools.streaming import stream_files

    def process_chunk(events, sample_info):
        '''Apply cuts to one chunk of events. Return dict of results.'''
        mask = events.Muon_pt > 20
        ...
        return {"n_passing": int(ak.sum(mask)), ...}

    results = stream_files(
        file_urls=["root://eospublic.cern.ch//eos/..."],
        branches=["Muon_pt", "Muon_eta", ...],
        process_fn=process_chunk,
        sample_info={"process": "qqZZ", "role": "background"},
    )
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import uproot


def _process_one_file(url, branches, process_fn, sample_info, step_size):
    """Process a single ROOT file. Returns per-chunk results and timing."""
    results = []
    n_events = 0
    t0 = time.time()
    try:
        with uproot.open({url: "Events"}, timeout=120) as tree:
            for events in tree.iterate(branches, step_size=step_size, library="ak"):
                n_events += len(events)
                chunk_result = process_fn(events, sample_info)
                results.append(chunk_result)
    except Exception as e:
        return {
            "error": str(e),
            "url": url,
            "n_events": 0,
            "chunks": [],
            "elapsed_s": time.time() - t0,
        }

    return {
        "url": url,
        "n_events": n_events,
        "chunks": results,
        "error": None,
        "elapsed_s": time.time() - t0,
    }


def _calibrate(file_urls, branches, process_fn, sample_info, n_probe=3):
    """Process a few files serially to measure throughput and pick optimal settings.

    Returns (step_size, max_workers, probe_results).
    """
    n_probe = min(n_probe, len(file_urls))
    step_size = 100_000  # conservative start

    probe_results = []
    total_events = 0
    total_time = 0

    for url in file_urls[:n_probe]:
        result = _process_one_file(url, branches, process_fn, sample_info, step_size)
        probe_results.append(result)
        if not result.get("error"):
            total_events += result["n_events"]
            total_time += result["elapsed_s"]

    if total_time == 0 or total_events == 0:
        return step_size, 8, probe_results

    events_per_sec = total_events / total_time
    avg_file_time = total_time / n_probe

    # Pick workers based on memory headroom and requested CPU count.
    # Each worker holds step_size events in memory. At ~1KB/event with 10-20 branches,
    # 100K events ≈ 100 MB per worker. Stay under ~16 GB total.
    # xrootd + uproot is I/O-bound and the C extensions release the GIL, so
    # threads scale well past the Python-CPU-bound limit — we cap at 32 normally
    # and 64 for very slow files, letting large allocations (e.g. 128-core
    # Perlmutter nodes) actually use the cores they were granted.
    mem_per_worker_mb = step_size * len(branches) * 8 / 1e6  # rough estimate
    max_by_mem = max(2, int(16_000 / max(mem_per_worker_mb, 1)))
    max_by_files = len(file_urls)
    ncpus = int(os.environ.get("ANALYSIS_NCPUS", 8))
    max_workers = min(32, ncpus, max_by_mem, max_by_files)

    # If files are very fast (< 5s each), use fewer workers to reduce overhead
    if avg_file_time < 5:
        max_workers = min(max_workers, 4)

    # If files are very slow (> 60s each), try more workers (I/O bound)
    if avg_file_time > 60:
        max_workers = min(64, ncpus, max_by_mem, max_by_files)

    print(
        f"  Calibration: {n_probe} files, {events_per_sec:.0f} evt/s, "
        f"{avg_file_time:.1f}s/file → {max_workers} workers, step_size={step_size}"
    )

    return step_size, max_workers, probe_results


def stream_files(
    file_urls: list[str],
    branches: list[str],
    process_fn,
    sample_info: dict = None,
):
    """Stream and process ROOT files in parallel with auto-tuned settings.

    Args:
        file_urls: list of root:// URLs to process
        branches: NanoAOD branch names to read
        process_fn: function(events, sample_info) -> dict, applied to each chunk
        sample_info: metadata dict passed to process_fn (e.g. process name, role)

    Returns:
        dict with keys: file_results, total_events, n_files_ok, n_files_fail, elapsed_s
    """
    if sample_info is None:
        sample_info = {}

    n_files = len(file_urls)
    if n_files == 0:
        return {
            "file_results": [],
            "total_events": 0,
            "n_files_ok": 0,
            "n_files_fail": 0,
            "elapsed_s": 0,
        }

    t0 = time.time()

    # Calibrate on first few files
    step_size, max_workers, probe_results = _calibrate(file_urls, branches, process_fn, sample_info)

    # Collect probe results
    file_results = list(probe_results)
    n_ok = sum(1 for r in probe_results if not r.get("error"))
    n_fail = sum(1 for r in probe_results if r.get("error"))
    n_probed = len(probe_results)

    remaining_urls = file_urls[n_probed:]

    if remaining_urls:
        print(f"  Streaming {len(remaining_urls)} remaining files ({max_workers} workers)")

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _process_one_file, url, branches, process_fn, sample_info, step_size
                ): url
                for url in remaining_urls
            }
            for future in as_completed(futures):
                result = future.result()
                file_results.append(result)
                if result.get("error"):
                    n_fail += 1
                    print(f"    WARN: {result['url'].split('/')[-1]}: {result['error'][:80]}")
                else:
                    n_ok += 1

                done = n_ok + n_fail
                if done % 10 == 0 or done == n_files:
                    elapsed = time.time() - t0
                    rate = elapsed / done if done > 0 else 0
                    eta = rate * (n_files - done) / 60
                    print(f"    [{done}/{n_files}] {rate:.1f}s/file, ETA {eta:.0f} min")

    elapsed = time.time() - t0
    total_events = sum(r.get("n_events", 0) for r in file_results)
    print(f"  Done: {n_ok} ok, {n_fail} failed, {total_events:,} events in {elapsed:.0f}s")

    return {
        "file_results": file_results,
        "total_events": total_events,
        "n_files_ok": n_ok,
        "n_files_fail": n_fail,
        "elapsed_s": elapsed,
    }
