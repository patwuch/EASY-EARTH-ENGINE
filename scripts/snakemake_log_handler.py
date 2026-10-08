"""
Snakemake --log-handler-script for the GEE batch processor.


State machine per job:
    pending  →  running   (job_info fires)
    running  →  done      (info "Finished job N." fires)
    running  →  failed    (job_error fires)

The module maintains a jobid→wildcards mapping in memory because Snakemake's
completion message only carries the numeric jobid, not the wildcards.
"""

import os
import re
import sys
import threading
import time
import duckdb
from datetime import datetime, timezone


def _open_log_shared(path: str):
    """
    Open a log file for reading in a way that allows other processes to delete
    it while we hold the handle — necessary on Windows to avoid PermissionError
    when Snakemake removes the log before re-running an incomplete job.

    On Linux, regular open() already allows concurrent deletion via unlink().
    On Windows, we must pass FILE_SHARE_DELETE to CreateFileW explicitly.
    """
    if sys.platform == "win32":
        import ctypes
        import ctypes.wintypes
        import msvcrt
        GENERIC_READ          = 0x80000000
        FILE_SHARE_READ       = 0x1
        FILE_SHARE_WRITE      = 0x2
        FILE_SHARE_DELETE     = 0x4
        OPEN_EXISTING         = 3
        FILE_ATTRIBUTE_NORMAL = 0x80
        handle = ctypes.windll.kernel32.CreateFileW(
            path,
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None,
        )
        if handle == ctypes.wintypes.HANDLE(-1).value:
            raise OSError(f"Cannot open {path}")
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)
        return os.fdopen(fd, "r", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")

# ── context injected by main.py via environment variables ────────────────────
RUN_ID  = os.environ.get("GEE_RUN_ID")
DB_PATH = os.environ.get("GEE_DB_PATH")

# In-memory map: jobid (int) → {"prod": ..., "band": ..., "time_chunk": ...}
# Populated on job_info, consumed on completion/error.
_job_map: dict[int, dict] = {}

# ── per-job log tail threads ──────────────────────────────────────────────────
# jobid (int) → threading.Event  (set to stop the tail thread)
_tail_stop: dict[int, threading.Event] = {}


def _tail_job_log(log_path: str, stop: threading.Event, prefix: str, line_filter=None):
    """
    Read new lines from a per-job log file and print them to stdout so they
    appear in snakemake_run.log (Snakemake redirects this process's stdout there).
    Waits up to 30 s for the log file to be created (worker import overhead),
    then tails from that point. Polls every 2 s until stop is set, then drains.
    line_filter: optional callable(str) -> bool; only lines returning True are forwarded.

    Opens and closes the file on each poll cycle so Windows does not hold a
    lock that prevents Snakemake from deleting the log before re-running a job.
    If the file is deleted and recreated (e.g. on a Snakemake-triggered restart),
    the tracked read position is reset so the new file is tailed from the start.
    """
    # Wait for the file to appear — heavy workers (ee + geopandas imports) can
    # take several seconds before writing their first log line.
    deadline = time.time() + 30
    while not os.path.exists(log_path):
        if time.time() > deadline or stop.is_set():
            return
        time.sleep(0.5)

    pos = 0  # byte offset into the current file
    while not stop.is_set():
        try:
            with _open_log_shared(log_path) as fh:
                # Detect file recreation: if the file is now shorter than our
                # tracked position, Snakemake deleted it and the worker wrote a
                # new one — reset so we tail from the beginning of the new file.
                fh.seek(0, 2)
                if fh.tell() < pos:
                    pos = 0
                fh.seek(pos)
                while True:
                    line = fh.readline()
                    if not line:
                        break
                    if line_filter is None or line_filter(line):
                        print(f"[job:{prefix}] {line}", end="", flush=True)
                pos = fh.tell()
        except OSError:
            pass  # file transiently absent between deletion and recreation
        stop.wait(2)

    # Drain any final lines written after the job finished.
    try:
        with _open_log_shared(log_path) as fh:
            fh.seek(0, 2)
            if fh.tell() < pos:
                pos = 0
            fh.seek(pos)
            for line in fh:
                if line_filter is None or line_filter(line):
                    print(f"[job:{prefix}] {line}", end="", flush=True)
    except Exception:
        pass


def _start_tail(jobid: int, log_path: str, prefix: str, line_filter=None):
    stop = threading.Event()
    _tail_stop[jobid] = stop
    t = threading.Thread(target=_tail_job_log, args=(log_path, stop, prefix, line_filter), daemon=True)
    t.start()


def _parquet_line_filter(line: str) -> bool:
    """Forward only the conversion start and summary lines."""
    return any(k in line for k in ["Converting ", "✓", "ERROR", "WARNING"])


def _merge_line_filter(line: str) -> bool:
    """Forward chunk-loading milestones and the final summary."""
    return any(k in line for k in ["Loading ", "Loaded ", "✓", "ERROR", "WARNING"])


def _stop_tail(jobid: int):
    stop = _tail_stop.pop(jobid, None)
    if stop:
        stop.set()


def _wildcards_to_dict(wildcards) -> dict:
    """Normalise Snakemake's Wildcards object or plain dict to a plain dict."""
    if wildcards is None:
        return {}
    if isinstance(wildcards, dict):
        return wildcards
    # Snakemake Wildcards is a namedtuple-like object
    try:
        return wildcards._asdict()
    except AttributeError:
        pass
    try:
        return dict(wildcards)
    except Exception:
        return {}


def _append_run_event(message: str, event_type: str = "info"):
    """Write a single event to run_events. Silently ignores all errors."""
    if not RUN_ID or not DB_PATH:
        return
    now = datetime.now(timezone.utc).isoformat()
    for attempt in range(3):
        try:
            with duckdb.connect(DB_PATH) as conn:
                conn.execute(
                    """INSERT INTO run_events
                           (event_time, run_id, event_type, status, message, payload_json)
                       VALUES (?, ?, ?, ?, ?, '{}')""",
                    [now, RUN_ID, event_type, event_type, message],
                )
            return
        except Exception:
            if attempt < 2:
                time.sleep(0.05 * (attempt + 1))


def _upsert_job(prod: str, band: str, chunk: str, status: str,
                jobid: int | None = None,
                log_path: str | None = None,
                error: str | None = None):
    """Write a single job status update to DuckDB. Silently ignores all errors."""
    if not RUN_ID or not DB_PATH:
        return
    if not prod or not band or not chunk:
        return

    now = datetime.now(timezone.utc).isoformat()
    started_at  = now if status == "running" else None
    finished_at = now if status in ("done", "failed") else None

    for attempt in range(3):
        try:
            with duckdb.connect(DB_PATH) as conn:
                conn.execute(
                    """
                    INSERT INTO jobs
                        (run_id, product, band, time_chunk, status,
                         jobid, log_path, started_at, finished_at, error)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (run_id, product, band, time_chunk) DO UPDATE SET
                        status      = excluded.status,
                        jobid       = COALESCE(excluded.jobid,      jobs.jobid),
                        log_path    = COALESCE(excluded.log_path,   jobs.log_path),
                        started_at  = COALESCE(excluded.started_at, jobs.started_at),
                        finished_at = excluded.finished_at,
                        error       = excluded.error
                    """,
                    [RUN_ID, prod, band, chunk, status,
                     jobid, log_path, started_at, finished_at, error],
                )
            return  # success
        except Exception:
            if attempt < 2:
                time.sleep(0.05 * (attempt + 1))  # brief back-off on contention


def log_handler(log: dict):
    """Entry point called by Snakemake for every log event."""
    try:
        _dispatch(log)
    except Exception:
        pass  # never let the handler crash the pipeline


def _dispatch(log: dict):
    level = log.get("level", "")

    # ── job dispatched ────────────────────────────────────────────────────────
    if level == "job_info":
        rule  = log.get("name") or ""
        jobid = log.get("jobid")
        wc    = _wildcards_to_dict(log.get("wildcards"))
        prod  = wc.get("prod")

        if rule == "merge_product_parquet":
            if prod:
                _append_run_event(f"Started {prod} merge", event_type="job_start")
            log_files = log.get("log") or []
            log_path  = log_files[0] if log_files else None
            if jobid is not None:
                _job_map[int(jobid)] = {"type": "merge", "prod": prod}
                if log_path:
                    _start_tail(int(jobid), log_path, f"merge/{prod}", _merge_line_filter)
            return

        if rule in ("preprocess_aoi", "preprocess_borders"):
            log_files = log.get("log") or []
            log_path  = log_files[0] if log_files else None
            if jobid is not None and log_path:
                _start_tail(int(jobid), log_path, rule)
            return

        # Only track the GEE extraction step for job status — convert_to_parquet
        # shares the same (prod, band, chunk) key and would regress "done" back to "running".
        if rule not in ("", "extract_geojson_chunk", "convert_to_parquet"):
            return

        band      = wc.get("band")
        chunk     = wc.get("time_chunk")
        log_files = log.get("log") or []
        log_path  = log_files[0] if log_files else None

        if rule == "convert_to_parquet":
            # Tail conversion logs (key lines only); do not touch job status in DuckDB.
            if jobid is not None and log_path:
                _start_tail(int(jobid), log_path, f"{band}/{chunk}:parquet", _parquet_line_filter)
            return

        if jobid is not None and prod and band and chunk:
            jid = int(jobid)
            _job_map[jid] = {"prod": prod, "band": band, "chunk": chunk}
            _upsert_job(prod, band, chunk, "running", jobid=jid, log_path=log_path)
            if log_path:
                _start_tail(jid, log_path, f"{band}/{chunk}")

    # ── job failed ────────────────────────────────────────────────────────────
    elif level == "job_error":
        rule  = log.get("name") or ""
        jobid = log.get("jobid")
        wc    = _wildcards_to_dict(log.get("wildcards"))
        prod  = wc.get("prod")
        band  = wc.get("band")
        chunk = wc.get("time_chunk")
        log_files = log.get("log") or []
        log_path  = log_files[0] if log_files else None

        exc = log.get("exception")
        error_str = str(exc) if exc else "job failed"
        if len(error_str) > 500:
            error_str = error_str[:500] + "…"

        if rule == "merge_product_parquet":
            if jobid is not None:
                _stop_tail(int(jobid))
                _job_map.pop(int(jobid), None)
            if prod:
                _append_run_event(f"Failed {prod} merge: {error_str}", event_type="job_error")
            return

        if rule == "convert_to_parquet":
            if jobid is not None:
                _stop_tail(int(jobid))
            return

        if jobid is not None:
            _stop_tail(int(jobid))
        if prod and band and chunk:
            _upsert_job(prod, band, chunk, "failed",
                        jobid=int(jobid) if jobid is not None else None,
                        log_path=log_path, error=error_str)
        elif jobid is not None:
            wc_cached = _job_map.get(int(jobid), {})
            if wc_cached:
                _upsert_job(wc_cached["prod"], wc_cached["band"], wc_cached["chunk"],
                            "failed", jobid=int(jobid), error=error_str)

    # ── job finished successfully ─────────────────────────────────────────────
    # Snakemake 7 sends level="job_finished" with jobid as a direct field.
    elif level == "job_finished":
        jobid = log.get("jobid")
        if jobid is not None:
            jobid = int(jobid)
            _stop_tail(jobid)
            wc_cached = _job_map.pop(jobid, {})
            if wc_cached.get("type") == "merge":
                prod = wc_cached.get("prod")
                if prod:
                    _append_run_event(f"Finished {prod} merge", event_type="job_finished")
            elif wc_cached:
                _upsert_job(wc_cached["prod"], wc_cached["band"], wc_cached["chunk"],
                            "done", jobid=jobid)
