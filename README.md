# vllm-monitor

Live terminal dashboard for monitoring [vLLM](https://github.com/vllm-project/vllm) inference services and GPU stats.

![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)
![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)

## Features

- **Real-time TUI dashboard** — curses-based, works over SSH
- **Multi-service monitoring** — track multiple vLLM instances simultaneously
- **GPU metrics** — temperature, utilization, memory, power draw (multi-GPU support)
- **Throughput tracking** — generation/prefill tokens per second with sparkline history
- **KV cache monitoring** — usage percentage with color-coded alerts
- **Latency stats** — average end-to-end and time-to-first-token
- **Utilization history** — 1m/5m/15m busy percentages per service
- **Auto-discovery** — find running vLLM services via port scanning and Docker
- **Alert thresholds** — terminal bell + optional webhook on KV cache or latency spikes
- **CSV logging** — append metrics to a file for later analysis
- **Non-interactive output** — `--once` for human-readable, `--json` for machine-readable
- **Zero dependencies** — stdlib only (optional PyYAML for YAML config files)

## Installation

```bash
pip install vllm-monitor
```

For YAML config file support:

```bash
pip install vllm-monitor[yaml]
```

## Quick Start

### Monitor a single service

```bash
vllm-monitor --url http://localhost:8000
```

### Monitor multiple services

```bash
vllm-monitor --url http://localhost:8000 --url http://localhost:8001
```

### Auto-discover running vLLM instances

```bash
vllm-monitor --discover
```

Save discovered services to a config file:

```bash
vllm-monitor --discover --save
```

### One-shot output (no TUI)

```bash
# Human-readable
vllm-monitor --url http://localhost:8000 --once

# JSON
vllm-monitor --url http://localhost:8000 --json
```

### CSV logging

```bash
vllm-monitor --url http://localhost:8000 --log metrics.csv
```

## Configuration

vllm-monitor looks for a config file at `~/.config/vllm-monitor/config.yaml` (or `.json`).

### YAML example

```yaml
services:
  - name: "My Model"
    url: "http://localhost:8000"
    timeout: 2
  - name: "Another Model"
    url: "http://localhost:8001"

refresh: 2

alerts:
  kv_cache_percent: 90
  latency_seconds: 30
  webhook_url: null
```

### JSON example

```json
{
  "services": [
    {"name": "My Model", "url": "http://localhost:8000", "timeout": 2}
  ],
  "refresh": 2,
  "alerts": {
    "kv_cache_percent": 90,
    "latency_seconds": 30,
    "webhook_url": null
  }
}
```

### Configuration resolution order

1. `--url` CLI arguments (highest priority)
2. `--config PATH` explicit config file
3. `~/.config/vllm-monitor/config.yaml`
4. `~/.config/vllm-monitor/config.json`

## CLI Reference

```
usage: vllm-monitor [-h] [--version] [--url URL] [--config PATH]
                    [--refresh N] [--no-gpu] [--discover]
                    [--discover-host HOST] [--save] [--once] [--json]
                    [--log PATH]

options:
  --url URL            vLLM base URL (repeatable)
  --config PATH        Path to config file (YAML or JSON)
  --refresh N          Refresh interval in seconds (default: 2)
  --no-gpu             Skip GPU metrics collection
  --discover           Auto-discover vLLM services on localhost
  --discover-host HOST Host to scan for discovery (default: localhost)
  --save               Save discovered services to config file
  --once               Print a single snapshot and exit
  --json               Print a JSON snapshot and exit
  --log PATH           Append CSV metrics each refresh cycle
```

## Remote Monitoring

To monitor vLLM services on a remote machine, use an SSH tunnel:

```bash
# Forward a single port
ssh -L 8000:localhost:8000 user@remote-host

# Forward multiple ports
ssh -L 8000:localhost:8000 -L 8001:localhost:8001 user@remote-host

# Then run locally
vllm-monitor --url http://localhost:8000 --url http://localhost:8001
```

## Keyboard Controls

| Key | Action |
|-----|--------|
| `q` | Quit |

## Requirements

- Python 3.10+
- `nvidia-smi` for GPU metrics (optional — runs without it)
- vLLM services exposing Prometheus metrics on `/metrics`

## License

MIT
