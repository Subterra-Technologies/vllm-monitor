"""Alert threshold checks with terminal bell and optional webhook."""

from __future__ import annotations

import json
import urllib.request

from vllm_monitor.types import AlertConfig, Snapshot


class AlertManager:
    """Track alert state and fire on transitions."""

    def __init__(self, config: AlertConfig):
        self.config = config
        # Track which alerts are currently firing per service
        self._active: dict[str, set[str]] = {}

    def check(self, service_name: str, snap: Snapshot) -> list[str]:
        """Check thresholds and return list of new alert messages."""
        if not snap.online:
            self._active.pop(service_name, None)
            return []

        current = set()
        messages = []

        if snap.kv_cache_pct >= self.config.kv_cache_percent:
            current.add("kv_cache")
            if "kv_cache" not in self._active.get(service_name, set()):
                messages.append(
                    f"[{service_name}] KV cache at {snap.kv_cache_pct:.0f}% "
                    f"(threshold: {self.config.kv_cache_percent:.0f}%)"
                )

        if snap.avg_e2e >= self.config.latency_seconds and snap.avg_e2e > 0:
            current.add("latency")
            if "latency" not in self._active.get(service_name, set()):
                messages.append(
                    f"[{service_name}] Latency at {snap.avg_e2e:.1f}s "
                    f"(threshold: {self.config.latency_seconds:.0f}s)"
                )

        self._active[service_name] = current
        return messages

    def fire(self, messages: list[str]) -> None:
        """Send alerts via terminal bell and optional webhook."""
        if not messages:
            return

        # Terminal bell
        print("\a", end="", flush=True)

        # Webhook
        if self.config.webhook_url:
            payload = json.dumps({"text": "\n".join(messages)}).encode()
            req = urllib.request.Request(
                self.config.webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urllib.request.urlopen(req, timeout=5)
            except Exception:
                pass

    @property
    def active_alerts(self) -> list[str]:
        """Return list of all currently active alert descriptions."""
        alerts = []
        for svc, kinds in self._active.items():
            for kind in kinds:
                if kind == "kv_cache":
                    alerts.append(f"{svc}: KV cache high")
                elif kind == "latency":
                    alerts.append(f"{svc}: latency high")
        return alerts
