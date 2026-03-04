"""CLI entry point with argparse and mode dispatch."""

from __future__ import annotations

import argparse
import curses
import sys

from vllm_monitor import __version__


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="vllm-monitor",
        description="Live terminal dashboard for monitoring vLLM inference services and GPU stats.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--url",
        action="append",
        dest="urls",
        metavar="URL",
        help="vLLM base URL (repeatable, e.g. --url http://localhost:8000)",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Path to config file (YAML or JSON)",
    )
    parser.add_argument(
        "--refresh",
        type=int,
        default=2,
        metavar="N",
        help="Refresh interval in seconds (default: 2)",
    )
    parser.add_argument(
        "--no-gpu",
        action="store_true",
        help="Skip GPU metrics collection",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Auto-discover vLLM services on localhost",
    )
    parser.add_argument(
        "--discover-host",
        default="localhost",
        metavar="HOST",
        help="Host to scan for discovery (default: localhost)",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save discovered services to config file (use with --discover)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Print a single snapshot and exit (no TUI)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print a JSON snapshot and exit (no TUI)",
    )
    parser.add_argument(
        "--log",
        metavar="PATH",
        help="Append CSV metrics to this file each refresh cycle",
    )

    args = parser.parse_args(argv)

    # Lazy imports to keep startup fast
    from vllm_monitor.config import load_config, save_config

    # Discovery mode
    if args.discover:
        from vllm_monitor.discovery import discover

        print(f"Scanning {args.discover_host} for vLLM services...")
        services = discover(args.discover_host)
        if not services:
            print("No vLLM services found.")
            sys.exit(1)

        print(f"\nFound {len(services)} service(s):\n")
        print(f"  {'Name':<30} {'URL':<35} {'Port'}")
        print(f"  {'-'*30} {'-'*35} {'-'*5}")
        for svc in services:
            print(f"  {svc.name:<30} {svc.url:<35} {svc.port}")

        if args.save:
            from vllm_monitor.types import MonitorConfig

            config = MonitorConfig(services=services, refresh=args.refresh)
            path = save_config(config)
            print(f"\nConfig saved to: {path}")
            return

        # If not saving, continue to monitor the discovered services
        if not args.once and not args.json:
            from vllm_monitor.types import MonitorConfig

            config = MonitorConfig(
                services=services,
                refresh=args.refresh,
                no_gpu=args.no_gpu,
                log_path=args.log,
            )
            _run_tui(config)
            return
        # Fall through to --once/--json with discovered services
        from vllm_monitor.types import MonitorConfig

        config = MonitorConfig(
            services=services,
            refresh=args.refresh,
            no_gpu=args.no_gpu,
        )
    else:
        config = load_config(
            config_path=args.config,
            urls=args.urls,
            refresh=args.refresh,
            no_gpu=args.no_gpu,
            log_path=args.log,
        )

    if not config.services:
        print(
            "No services configured. Use one of:\n"
            "  vllm-monitor --url http://localhost:8000\n"
            "  vllm-monitor --discover\n"
            "  vllm-monitor --config path/to/config.yaml\n"
            "\n"
            "Or create a config file at ~/.config/vllm-monitor/config.yaml",
            file=sys.stderr,
        )
        sys.exit(1)

    # Dispatch based on mode
    if args.json:
        from vllm_monitor.output import print_json

        print_json(config)
    elif args.once:
        from vllm_monitor.output import print_snapshot

        print_snapshot(config)
    else:
        _run_tui(config)


def _run_tui(config) -> None:
    """Launch the curses TUI dashboard."""
    from vllm_monitor.dashboard import draw
    from vllm_monitor.output import CsvLogger

    csv_logger = None
    if config.log_path:
        csv_logger = CsvLogger(config.log_path)
        csv_logger.open()

    try:
        curses.wrapper(lambda stdscr: draw(stdscr, config, csv_logger))
    except KeyboardInterrupt:
        pass
    finally:
        if csv_logger:
            csv_logger.close()
