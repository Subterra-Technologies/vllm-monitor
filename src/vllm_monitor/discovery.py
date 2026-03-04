"""Auto-discovery of vLLM services via port scanning and Docker inspection."""

from __future__ import annotations

import json
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from vllm_monitor.types import ServiceConfig


def _probe_port(host: str, port: int, timeout: float = 1.0) -> ServiceConfig | None:
    """Check if a port serves vLLM metrics."""
    url = f"http://{host}:{port}/metrics"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read().decode(errors="replace")
        if "vllm:" in body:
            # Try to extract model name
            name = f"vLLM :{port}"
            for line in body.splitlines():
                if line.startswith("vllm:num_requests_running"):
                    # Extract model_name label if present
                    if 'model_name="' in line:
                        start = line.index('model_name="') + len('model_name="')
                        end = line.index('"', start)
                        name = line[start:end]
                    break
            return ServiceConfig(
                name=name, url=f"http://{host}:{port}", timeout=timeout
            )
    except Exception:
        pass
    return None


def scan_ports(
    host: str = "localhost",
    ports: range | None = None,
    max_workers: int = 20,
) -> list[ServiceConfig]:
    """Scan a range of ports for vLLM services."""
    if ports is None:
        ports = range(8000, 8101)

    results: list[ServiceConfig] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_probe_port, host, p): p for p in ports}
        for future in as_completed(futures):
            svc = future.result()
            if svc is not None:
                results.append(svc)

    results.sort(key=lambda s: s.port)
    return results


def scan_docker(image_filter: str = "vllm") -> list[ServiceConfig]:
    """Detect vLLM services from running Docker containers."""
    try:
        raw = subprocess.check_output(
            ["docker", "ps", "--format", "{{json .}}"],
            timeout=5,
            stderr=subprocess.DEVNULL,
        ).decode()
    except Exception:
        return []

    results: list[ServiceConfig] = []
    for line in raw.strip().splitlines():
        if not line.strip():
            continue
        try:
            container = json.loads(line)
        except json.JSONDecodeError:
            continue

        image = container.get("Image", "")
        if image_filter.lower() not in image.lower():
            continue

        # Parse port mappings like "0.0.0.0:8000->8000/tcp"
        ports_str = container.get("Ports", "")
        for mapping in ports_str.split(","):
            mapping = mapping.strip()
            if "->" in mapping:
                host_part = mapping.split("->")[0]
                # Extract port from "0.0.0.0:8000" or "8000"
                if ":" in host_part:
                    port_str = host_part.rsplit(":", 1)[1]
                else:
                    port_str = host_part
                try:
                    port = int(port_str)
                except ValueError:
                    continue
                name = container.get("Names", f"vllm-{port}")
                results.append(
                    ServiceConfig(
                        name=name,
                        url=f"http://localhost:{port}",
                    )
                )

    return results


def discover(host: str = "localhost") -> list[ServiceConfig]:
    """Run all discovery strategies and return deduplicated results."""
    services: list[ServiceConfig] = []
    seen_ports: set[int] = set()

    # Port scan
    for svc in scan_ports(host):
        if svc.port not in seen_ports:
            services.append(svc)
            seen_ports.add(svc.port)

    # Docker
    for svc in scan_docker():
        if svc.port not in seen_ports:
            services.append(svc)
            seen_ports.add(svc.port)

    return services
