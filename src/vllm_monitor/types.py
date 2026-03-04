"""Data types for vllm-monitor."""

from __future__ import annotations

import dataclasses
from urllib.parse import urlparse


@dataclasses.dataclass
class ServiceConfig:
    """A vLLM service endpoint."""

    name: str
    url: str
    timeout: float = 2.0

    @property
    def port(self) -> int:
        parsed = urlparse(self.url)
        if parsed.port:
            return parsed.port
        return 443 if parsed.scheme == "https" else 80

    @property
    def metrics_url(self) -> str:
        return self.url.rstrip("/") + "/metrics"


@dataclasses.dataclass
class GpuInfo:
    """GPU sensor data from nvidia-smi."""

    index: int = 0
    name: str = ""
    temp: int = 0
    temp_limit: int = 0
    gpu_util: int = 0
    mem_util: int = 0
    power: float = 0.0
    power_inst: float = 0.0
    clock_gr: int = 0
    clock_max_gr: int = 0
    mem_total_mb: int = 0
    mem_used_mb: int = 0
    mem_avail_mb: int = 0
    is_unified_memory: bool = False
    procs: list = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class AlertConfig:
    """Alert threshold configuration."""

    kv_cache_percent: float = 90.0
    latency_seconds: float = 30.0
    webhook_url: str | None = None


@dataclasses.dataclass
class MonitorConfig:
    """Top-level configuration for the monitor."""

    services: list[ServiceConfig] = dataclasses.field(default_factory=list)
    refresh: int = 2
    no_gpu: bool = False
    alerts: AlertConfig = dataclasses.field(default_factory=AlertConfig)
    log_path: str | None = None


@dataclasses.dataclass
class Snapshot:
    """A point-in-time snapshot of a single service's metrics."""

    service_name: str = ""
    url: str = ""
    online: bool = False
    running: float = 0.0
    waiting: float = 0.0
    kv_cache_pct: float = 0.0
    gen_tps: float = 0.0
    prompt_tps: float = 0.0
    req_ps: float = 0.0
    gen_total: float = 0.0
    prompt_total: float = 0.0
    req_count: float = 0.0
    avg_e2e: float = 0.0
    avg_ttft: float = 0.0
    preemptions: float = 0.0
    prefix_hit_rate: float = 0.0
