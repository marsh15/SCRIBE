"""Orchestrates one or more scale steps and builds the result document."""

from __future__ import annotations

import asyncio
import platform
import subprocess
import time
from typing import Callable

import psycopg

from scribe_intel.retrieval.dense import AUTO_ANN_THRESHOLD

from . import measure
from .load import HNSW_EF_CONSTRUCTION, HNSW_M, Loaded, load_scale
from .pgdb import disk_free_gb, drop_database, ensure_schema, require_throwaway
from .synth import lexical_queries, make_vocab

BIG_SCALE = 100_000
MIN_FREE_GB_BIG = 8.0
MIN_FREE_GB_DEFAULT = 2.0


def cycle_to(items: list, n: int) -> list:
    """First n items, repeating the list when it is shorter (small workspaces have few documents)."""
    if not items:
        return []
    return [items[i % len(items)] for i in range(n)]


def required_free_gb(scale: int) -> float:
    return MIN_FREE_GB_BIG if scale >= BIG_SCALE else MIN_FREE_GB_DEFAULT


def _sh(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001 - hardware discovery is best effort
        return ""


def hardware() -> dict:
    return {"macos": _sh(["sw_vers", "-productVersion"]), "chip": _sh(["sysctl", "-n", "machdep.cpu.brand_string"]),
            "cpus": _sh(["sysctl", "-n", "hw.ncpu"]), "mem_bytes": _sh(["sysctl", "-n", "hw.memsize"]),
            "python": platform.python_version(), "docker_vm": _sh(["docker", "info", "--format",
                                                                   "{{.OperatingSystem}} cpus={{.NCPU}} mem_bytes={{.MemTotal}}"])}


async def measure_loaded(url: str, loaded: Loaded, *, n_queries: int, concurrency_modes: list[str]) -> dict:
    ws = [{"index": w.index, "id": str(w.id), "n_chunks": w.n_children} for w in loaded.workspaces]
    queries = {k: cycle_to(v, n_queries) for k, v in loaded.queries.items()}
    terms = lexical_queries(loaded.seed, make_vocab(loaded.seed), n_queries)
    async with await psycopg.AsyncConnection.connect(url, autocommit=True, prepare_threshold=None) as conn:
        env = await measure.environment(conn)
        res: dict = {"environment": env, "workspaces": ws}
        res["storage"] = await measure.storage_section(conn, sum(w["n_chunks"] for w in ws))
        res["dense"] = await measure.dense_section(conn, ws, loaded.index_profile_id, queries, env["iterative_scan_supported"])
        res["lexical"] = await measure.lexical_section(conn, ws, loaded.index_profile_id, terms)
    main = ws[0]
    res["concurrency"] = {}
    for mode in concurrency_modes:
        res["concurrency"][mode] = await measure.concurrency_section(url, main["id"], loaded.index_profile_id,
                                                                     queries[main["id"]], mode)
    return res


def run_scales(url: str, scales: list[int], *, seed: int, n_queries: int, drop_each: bool, log: Callable[[str], None],
               final_concurrency_modes=("auto", "exact", "ann")) -> dict:
    require_throwaway(url)
    doc: dict = {"seed": seed, "hardware": hardware(), "hnsw": {"m": HNSW_M, "ef_construction": HNSW_EF_CONSTRUCTION},
                 "auto_ann_threshold_in_code": AUTO_ANN_THRESHOLD, "k": measure.K, "n_queries": n_queries, "scales": []}
    for idx, scale in enumerate(scales):
        free = disk_free_gb()
        entry: dict = {"scale": scale, "disk_free_gb_before": free}
        need = required_free_gb(scale)
        if free < need:
            entry["skipped"] = f"free disk {free} GB < required {need} GB for scale {scale}"
            log(f"SKIP {scale}: {entry['skipped']}")
            doc["scales"].append(entry)
            continue
        t0 = time.perf_counter()
        ensure_schema(url)
        loaded = load_scale(url, scale, seed)
        entry["load_and_index_s"] = round(time.perf_counter() - t0, 1)
        entry["build_s"] = loaded.build_s
        entry["disk_free_gb_after_load"] = disk_free_gb()
        log(f"loaded {scale} in {entry['load_and_index_s']}s; free {entry['disk_free_gb_after_load']} GB")
        modes = list(final_concurrency_modes) if idx == len(scales) - 1 else ["auto"]
        entry.update(asyncio.run(measure_loaded(url, loaded, n_queries=n_queries, concurrency_modes=modes)))
        entry["disk_free_gb_after_measure"] = disk_free_gb()
        doc["scales"].append(entry)
        if drop_each or entry["disk_free_gb_after_measure"] < 6.0:
            drop_database(url)
            entry["database_dropped_after_step"] = True
            entry["disk_free_gb_after_drop"] = disk_free_gb()
            log(f"dropped database; free {entry['disk_free_gb_after_drop']} GB")
    return doc
