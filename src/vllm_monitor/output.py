"""Non-curses output modes: --once, --json, and CSV logging."""

from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

from vllm_monitor.collector import SnapshotCollector, fetch_gpu_info, fmt_tokens
from vllm_monitor.types import MonitorConfig, Snapshot


def print_snapshot(config: MonitorConfig) -> None:
    """Print a human-readable table of all services, then exit."""
    collector = SnapshotCollector()
    gpus = [] if config.no_gpu else fetch_gpu_info()

    # GPU info
    if gpus:
        for gpu in gpus:
            label = gpu.name or f"GPU {gpu.index}"
            mem_type = "Unified" if gpu.is_unified_memory else "VRAM"
            mem_pct = (
                f"{gpu.mem_used_mb / gpu.mem_total_mb * 100:.0f}%"
                if gpu.mem_total_mb > 0
                else "N/A"
            )
            print(f"GPU: {label}")
            print(
                f"  Temp: {gpu.temp}C  Util: {gpu.gpu_util}%  "
                f"{mem_type}: {gpu.mem_used_mb}MiB / {gpu.mem_total_mb}MiB ({mem_pct})  "
                f"Power: {gpu.power:.1f}W"
            )
        print()

    # Services
    for svc in config.services:
        snap = collector.collect(svc)
        status = "UP" if snap.online else "OFFLINE"
        print(f"{snap.service_name} ({svc.url}) [{status}]")
        if not snap.online:
            print()
            continue
        print(
            f"  Requests: running={int(snap.running)}  waiting={int(snap.waiting)}  "
            f"total={int(snap.req_count)}"
        )
        print(f"  KV Cache: {snap.kv_cache_pct:.1f}%")
        print(
            f"  Tokens: generated={fmt_tokens(snap.gen_total)}  "
            f"prompt={fmt_tokens(snap.prompt_total)}"
        )
        print(
            f"  Latency: avg_e2e={snap.avg_e2e:.2f}s  avg_ttft={snap.avg_ttft:.3f}s"
        )
        print(
            f"  Prefix cache hit rate: {snap.prefix_hit_rate:.0f}%  "
            f"preemptions={int(snap.preemptions)}"
        )
        print()


def print_json(config: MonitorConfig) -> None:
    """Print a JSON snapshot of all services, then exit."""
    collector = SnapshotCollector()
    gpus = [] if config.no_gpu else fetch_gpu_info()

    import dataclasses

    output = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "gpus": [dataclasses.asdict(g) for g in gpus],
        "services": [],
    }

    for svc in config.services:
        snap = collector.collect(svc)
        output["services"].append(dataclasses.asdict(snap))

    json.dump(output, sys.stdout, indent=2)
    sys.stdout.write("\n")


CSV_COLUMNS = [
    "timestamp",
    "service_name",
    "url",
    "status",
    "running",
    "waiting",
    "kv_cache_pct",
    "gen_tps",
    "prompt_tps",
    "avg_e2e",
    "avg_ttft",
    "gpu_temp",
    "gpu_util",
    "mem_used_pct",
    "power_w",
]


class CsvLogger:
    """Append metrics rows to a CSV file each refresh cycle."""

    def __init__(self, path: str):
        self.path = Path(path)
        self._file = None
        self._writer = None

    def open(self) -> None:
        is_new = not self.path.exists() or self.path.stat().st_size == 0
        self._file = open(self.path, "a", newline="")
        self._writer = csv.writer(self._file)
        if is_new:
            self._writer.writerow(CSV_COLUMNS)
            self._file.flush()

    def write_row(self, snap: Snapshot, gpus: list) -> None:
        if self._writer is None:
            return
        gpu = gpus[0] if gpus else None
        gpu_temp = gpu.temp if gpu else 0
        gpu_util = gpu.gpu_util if gpu else 0
        mem_pct = (
            (gpu.mem_used_mb / gpu.mem_total_mb * 100)
            if gpu and gpu.mem_total_mb > 0
            else 0
        )
        power = gpu.power if gpu else 0

        self._writer.writerow([
            time.strftime("%Y-%m-%dT%H:%M:%S"),
            snap.service_name,
            snap.url,
            "UP" if snap.online else "OFFLINE",
            int(snap.running),
            int(snap.waiting),
            f"{snap.kv_cache_pct:.1f}",
            f"{snap.gen_tps:.1f}",
            f"{snap.prompt_tps:.1f}",
            f"{snap.avg_e2e:.2f}",
            f"{snap.avg_ttft:.3f}",
            gpu_temp,
            gpu_util,
            f"{mem_pct:.1f}",
            f"{power:.1f}",
        ])
        self._file.flush()

    def close(self) -> None:
        if self._file:
            self._file.close()
