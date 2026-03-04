"""Data collection from vLLM metrics endpoints and nvidia-smi."""

from __future__ import annotations

import re
import subprocess
import time
import urllib.request

from vllm_monitor.types import GpuInfo, ServiceConfig, Snapshot


def fetch_metrics(url: str, timeout: float = 2.0) -> dict[str, float] | None:
    """Fetch and parse Prometheus metrics from a vLLM endpoint."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            lines = r.read().decode().splitlines()
    except Exception:
        return None
    metrics: dict[str, float] = {}
    for line in lines:
        if line.startswith("#") or not line.strip():
            continue
        try:
            key_part, val = line.rsplit(" ", 1)
            metrics[key_part] = float(val)
        except ValueError:
            continue
    return metrics


def get_val(metrics: dict[str, float], prefix: str, default: float = 0.0) -> float:
    """Get first metric value matching a prefix."""
    for k, v in metrics.items():
        if k.startswith(prefix):
            return v
    return default


def fmt_tokens(n: float) -> str:
    """Format a token count for compact display."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{int(n)}"


def _find(pattern: str, text: str, conv=int):
    """Extract a value from text using a regex pattern."""
    m = re.search(pattern, text)
    if m:
        try:
            return conv(m.group(1))
        except (ValueError, TypeError):
            pass
    return 0


def _parse_gpu_section(raw: str, index: int = 0) -> GpuInfo:
    """Parse a single GPU section from nvidia-smi -q output."""
    info = GpuInfo(index=index)

    # Try to extract GPU name
    m = re.search(r"Product Name\s*:\s*(.+)", raw)
    if m:
        info.name = m.group(1).strip()

    info.temp = _find(r"GPU Current Temp\s*:\s*(\d+)", raw)
    info.temp_limit = _find(r"GPU T\.Limit Temp\s*:\s*(\d+)", raw)
    info.gpu_util = _find(r"Gpu\s*:\s*(\d+)\s*%", raw)
    if info.gpu_util == 0:
        info.gpu_util = _find(r"GPU\s*:\s*(\d+)\s*%", raw)
    info.mem_util = _find(r"Memory\s*:\s*(\d+)\s*%", raw)
    info.power = _find(r"Average Power Draw\s*:\s*([\d.]+)", raw, float)
    info.power_inst = _find(r"Instantaneous Power Draw\s*:\s*([\d.]+)", raw, float)
    info.clock_gr = _find(r"Graphics\s*:\s*(\d+)\s*MHz", raw)
    info.clock_max_gr = _find(r"Max Clocks[\s\S]*?Graphics\s*:\s*(\d+)\s*MHz", raw)

    # Per-process GPU memory
    for m in re.finditer(
        r"Process ID\s*:\s*(\d+)\s+Type\s*:\s*\w\s+Name\s*:\s*(.+?)\s+"
        r"Used GPU Memory\s*:\s*(\d+)\s*MiB",
        raw,
    ):
        info.procs.append({
            "pid": m.group(1),
            "name": m.group(2).strip().split("/")[-1],
            "mem_mib": int(m.group(3)),
        })

    return info


def fetch_gpu_info() -> list[GpuInfo]:
    """Return list of GPU info dicts via nvidia-smi, one per GPU."""
    try:
        raw = subprocess.check_output(
            ["nvidia-smi", "-q"], timeout=3, stderr=subprocess.STDOUT
        ).decode()
    except Exception:
        return [GpuInfo()]

    # Split on GPU boundaries for multi-GPU
    gpu_sections = re.split(r"(?=^GPU \d+:)", raw, flags=re.MULTILINE)
    gpu_sections = [s for s in gpu_sections if s.strip()]

    gpus: list[GpuInfo] = []
    if len(gpu_sections) <= 1:
        # Single GPU or no clear boundaries — treat entire output as one GPU
        gpus.append(_parse_gpu_section(raw, 0))
    else:
        for i, section in enumerate(gpu_sections):
            gpus.append(_parse_gpu_section(section, i))

    # Detect unified memory (GB10) — if nvidia-smi reports no FB memory
    # or Product Name contains "GB10", use system memory
    is_unified = any("GB10" in g.name for g in gpus) or all(
        g.mem_total_mb == 0 for g in gpus
    )

    # System memory (unified memory on GB10 or general info)
    try:
        mem_raw = subprocess.check_output(["free", "-m"], timeout=2).decode()
        parts = mem_raw.splitlines()[1].split()
        mem_total = int(parts[1])
        mem_used = int(parts[2])
        mem_avail = int(parts[6]) if len(parts) > 6 else int(parts[3])
    except Exception:
        mem_total = mem_used = mem_avail = 0

    # Attach system memory to first GPU if unified, or to all
    for gpu in gpus:
        if is_unified or gpu.mem_total_mb == 0:
            gpu.mem_total_mb = mem_total
            gpu.mem_used_mb = mem_used
            gpu.mem_avail_mb = mem_avail
            gpu.is_unified_memory = True
        else:
            gpu.is_unified_memory = False

    return gpus if gpus else [GpuInfo()]


class SnapshotCollector:
    """Collects snapshots with throughput calculation (needs previous values)."""

    def __init__(self):
        self._prev_gen: dict[int, float] = {}
        self._prev_prompt: dict[int, float] = {}
        self._prev_req: dict[int, float] = {}
        self._prev_time: dict[int, float] = {}

    def collect(self, svc: ServiceConfig) -> Snapshot:
        """Fetch metrics for a service and compute a snapshot."""
        port = svc.port
        metrics = fetch_metrics(svc.metrics_url, timeout=svc.timeout)
        now = time.time()

        snap = Snapshot(service_name=svc.name, url=svc.url)

        if metrics is None:
            return snap

        snap.online = True
        snap.running = get_val(metrics, "vllm:num_requests_running")
        snap.waiting = get_val(metrics, "vllm:num_requests_waiting")
        snap.kv_cache_pct = get_val(metrics, "vllm:kv_cache_usage_perc") * 100
        snap.gen_total = get_val(metrics, "vllm:generation_tokens_total")
        snap.prompt_total = get_val(metrics, "vllm:prompt_tokens_total")
        snap.req_count = get_val(metrics, "vllm:request_success_total")

        e2e_sum = get_val(metrics, "vllm:e2e_request_latency_seconds_sum")
        e2e_count = get_val(metrics, "vllm:e2e_request_latency_seconds_count")
        ttft_sum = get_val(metrics, "vllm:time_to_first_token_seconds_sum")
        ttft_count = get_val(metrics, "vllm:time_to_first_token_seconds_count")

        snap.preemptions = get_val(metrics, "vllm:num_preemptions_total")
        prefix_hits = get_val(metrics, "vllm:prefix_cache_hits_total")
        prefix_queries = get_val(metrics, "vllm:prefix_cache_queries_total")
        snap.prefix_hit_rate = (
            (prefix_hits / prefix_queries * 100) if prefix_queries > 0 else 0.0
        )

        snap.avg_e2e = (e2e_sum / e2e_count) if e2e_count > 0 else 0.0
        snap.avg_ttft = (ttft_sum / ttft_count) if ttft_count > 0 else 0.0

        # Throughput calculation
        if port in self._prev_time and (now - self._prev_time[port]) > 0.5:
            dt = now - self._prev_time[port]
            snap.gen_tps = max(
                0, (snap.gen_total - self._prev_gen.get(port, snap.gen_total)) / dt
            )
            snap.prompt_tps = max(
                0,
                (snap.prompt_total - self._prev_prompt.get(port, snap.prompt_total))
                / dt,
            )
            snap.req_ps = max(
                0, (snap.req_count - self._prev_req.get(port, snap.req_count)) / dt
            )

        self._prev_gen[port] = snap.gen_total
        self._prev_prompt[port] = snap.prompt_total
        self._prev_req[port] = snap.req_count
        self._prev_time[port] = now

        return snap
