"""Configuration loading and saving with YAML/JSON fallback."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from vllm_monitor.types import AlertConfig, MonitorConfig, ServiceConfig

try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False


DEFAULT_CONFIG_DIR = Path.home() / ".config" / "vllm-monitor"
DEFAULT_CONFIG_YAML = DEFAULT_CONFIG_DIR / "config.yaml"
DEFAULT_CONFIG_JSON = DEFAULT_CONFIG_DIR / "config.json"

DEFAULT_CONFIG_COMMENT = """\
# vllm-monitor configuration
# See: https://github.com/subterra/vllm-monitor
#
# services:
#   - name: "My Model"
#     url: "http://localhost:8000"
#     timeout: 2
#
# refresh: 2
#
# alerts:
#   kv_cache_percent: 90
#   latency_seconds: 30
#   webhook_url: null
"""


def _parse_services(raw: list) -> list[ServiceConfig]:
    services = []
    for item in raw:
        if isinstance(item, str):
            services.append(ServiceConfig(name=item, url=item))
        elif isinstance(item, dict):
            services.append(
                ServiceConfig(
                    name=item.get("name", item.get("url", "unknown")),
                    url=item["url"],
                    timeout=item.get("timeout", 2.0),
                )
            )
    return services


def _parse_alerts(raw: dict | None) -> AlertConfig:
    if not raw:
        return AlertConfig()
    return AlertConfig(
        kv_cache_percent=raw.get("kv_cache_percent", 90.0),
        latency_seconds=raw.get("latency_seconds", 30.0),
        webhook_url=raw.get("webhook_url"),
    )


def load_file(path: Path) -> dict:
    """Load a config file (YAML or JSON based on extension)."""
    text = path.read_text()
    if path.suffix in (".yaml", ".yml"):
        if not HAS_YAML:
            print(
                "Config file is YAML but pyyaml is not installed.\n"
                "Install it with: pip install vllm-monitor[yaml]\n"
                "Or use a JSON config file instead.",
                file=sys.stderr,
            )
            sys.exit(1)
        return yaml.safe_load(text) or {}
    return json.loads(text)


def load_config(
    config_path: str | None = None,
    urls: list[str] | None = None,
    refresh: int = 2,
    no_gpu: bool = False,
    log_path: str | None = None,
) -> MonitorConfig:
    """Build a MonitorConfig from CLI args and/or config file.

    Resolution order: CLI --url > --config path > default config locations > error.
    """
    # CLI URLs take priority
    if urls:
        services = []
        for i, u in enumerate(urls):
            # Ensure URL has scheme
            if not u.startswith("http"):
                u = "http://" + u
            services.append(ServiceConfig(name=f"Service {i + 1}", url=u))
        return MonitorConfig(
            services=services,
            refresh=refresh,
            no_gpu=no_gpu,
            log_path=log_path,
        )

    # Try explicit config path
    if config_path:
        p = Path(config_path)
        if not p.exists():
            print(f"Config file not found: {config_path}", file=sys.stderr)
            sys.exit(1)
        data = load_file(p)
        return _build_config(data, refresh=refresh, no_gpu=no_gpu, log_path=log_path)

    # Try default locations
    for default_path in (DEFAULT_CONFIG_YAML, DEFAULT_CONFIG_JSON):
        if default_path.exists():
            data = load_file(default_path)
            return _build_config(
                data, refresh=refresh, no_gpu=no_gpu, log_path=log_path
            )

    # No config found — return empty config (caller should handle discovery or error)
    return MonitorConfig(refresh=refresh, no_gpu=no_gpu, log_path=log_path)


def _build_config(
    data: dict,
    refresh: int = 2,
    no_gpu: bool = False,
    log_path: str | None = None,
) -> MonitorConfig:
    """Build MonitorConfig from parsed config dict."""
    services = _parse_services(data.get("services", []))
    alerts = _parse_alerts(data.get("alerts"))
    return MonitorConfig(
        services=services,
        refresh=data.get("refresh", refresh),
        no_gpu=no_gpu,
        alerts=alerts,
        log_path=log_path,
    )


def save_config(config: MonitorConfig, path: Path | None = None) -> Path:
    """Save config to file. Returns the path written."""
    if path is None:
        path = DEFAULT_CONFIG_YAML if HAS_YAML else DEFAULT_CONFIG_JSON

    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "services": [
            {"name": s.name, "url": s.url, "timeout": s.timeout}
            for s in config.services
        ],
        "refresh": config.refresh,
        "alerts": {
            "kv_cache_percent": config.alerts.kv_cache_percent,
            "latency_seconds": config.alerts.latency_seconds,
            "webhook_url": config.alerts.webhook_url,
        },
    }

    if path.suffix in (".yaml", ".yml") and HAS_YAML:
        text = DEFAULT_CONFIG_COMMENT + "\n" + yaml.dump(
            data, default_flow_style=False, sort_keys=False
        )
    else:
        text = json.dumps(data, indent=2) + "\n"

    path.write_text(text)
    return path


def generate_default_config() -> Path:
    """Generate a default config file if none exists. Returns the path."""
    for p in (DEFAULT_CONFIG_YAML, DEFAULT_CONFIG_JSON):
        if p.exists():
            return p

    path = DEFAULT_CONFIG_YAML if HAS_YAML else DEFAULT_CONFIG_JSON
    path.parent.mkdir(parents=True, exist_ok=True)

    if HAS_YAML:
        path.write_text(DEFAULT_CONFIG_COMMENT)
    else:
        data = {
            "services": [
                {"name": "My Model", "url": "http://localhost:8000", "timeout": 2}
            ],
            "refresh": 2,
            "alerts": {
                "kv_cache_percent": 90,
                "latency_seconds": 30,
                "webhook_url": None,
            },
        }
        path.write_text(json.dumps(data, indent=2) + "\n")

    return path
