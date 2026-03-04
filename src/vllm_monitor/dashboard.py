"""Curses-based TUI dashboard."""

from __future__ import annotations

import curses
import time

from vllm_monitor.alerts import AlertManager
from vllm_monitor.collector import SnapshotCollector, fetch_gpu_info, fmt_tokens
from vllm_monitor.history import SparklineBuffer, UtilHistory
from vllm_monitor.output import CsvLogger
from vllm_monitor.types import GpuInfo, MonitorConfig, Snapshot


def safe_addnstr(stdscr, row: int, col: int, text: str, maxlen: int, attr: int):
    """addnstr that silently ignores curses errors at screen edges."""
    try:
        if maxlen > 0:
            stdscr.addnstr(row, col, text, maxlen, attr)
    except curses.error:
        pass


def _draw_bar(value: float, max_val: float, width: int = 20) -> str:
    """Render a [####----] style progress bar."""
    pct = min(value / max(max_val, 1) * 100, 100)
    filled = int(pct / 100 * width)
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def _color_for_pct(pct: float, green, yellow, red) -> int:
    if pct < 50:
        return green
    if pct < 80:
        return yellow
    return red


def _draw_gpu_panel(
    stdscr, row: int, gpu: GpuInfo, width: int, colors: dict
) -> int:
    """Draw GPU info panel. Returns the next row."""
    W = width
    GREEN = colors["GREEN"]
    YELLOW = colors["YELLOW"]
    RED = colors["RED"]
    WHITE = colors["WHITE"]
    MAGENTA = colors["MAGENTA"]
    DIM = colors["DIM"]
    BOLD = colors["BOLD"]
    bar_w = 20

    label = gpu.name or f"GPU {gpu.index}"
    mem_type = "Unified Memory" if gpu.is_unified_memory else "VRAM"
    safe_addnstr(stdscr, row, 0, f" GPU  {label} ({mem_type})", W, MAGENTA | BOLD)
    row += 1

    # Temperature
    t = gpu.temp
    tl = gpu.temp_limit
    t_color = GREEN if t < 55 else YELLOW if t < 70 else RED
    temp_str = f"{t}C"
    if tl > 0:
        temp_str += f" / {tl}C limit"
    t_pct = min(t / max(tl, 100) * 100, 100) if tl > 0 else min(t, 100)
    t_bar = _draw_bar(t_pct, 100, bar_w)
    safe_addnstr(stdscr, row, 2, "Temp:", W, WHITE)
    safe_addnstr(stdscr, row, 14, f"{t_bar} {temp_str}", W - 14, t_color)
    row += 1

    # GPU Utilization
    gu = gpu.gpu_util
    gu_color = GREEN if gu < 50 else YELLOW if gu < 80 else RED
    gu_bar = _draw_bar(gu, 100, bar_w)
    safe_addnstr(stdscr, row, 2, "GPU Util:", W, WHITE)
    safe_addnstr(stdscr, row, 14, f"{gu_bar} {gu}%", W - 14, gu_color)
    row += 1

    # Memory
    mt = gpu.mem_total_mb
    mu = gpu.mem_used_mb
    if mt > 0:
        mu_pct = mu / mt * 100
        m_color = GREEN if mu_pct < 60 else YELLOW if mu_pct < 85 else RED
        m_bar = _draw_bar(mu_pct, 100, bar_w)
        safe_addnstr(stdscr, row, 2, "Memory:", W, WHITE)
        safe_addnstr(
            stdscr,
            row,
            14,
            f"{m_bar} {mu / 1024:.1f} / {mt / 1024:.0f} GiB ({mu_pct:.0f}%)",
            W - 14,
            m_color,
        )
    row += 1

    # Power
    pw = gpu.power
    pi = gpu.power_inst
    safe_addnstr(stdscr, row, 2, "Power:", W, WHITE)
    safe_addnstr(stdscr, row, 14, f"avg={pw:.1f}W  now={pi:.1f}W", W - 14, DIM)
    col = 14 + len(f"avg={pw:.1f}W  now={pi:.1f}W") + 2
    safe_addnstr(stdscr, row, col, f"clock={gpu.clock_gr}MHz", W - col, DIM)
    if gpu.clock_max_gr:
        col2 = col + len(f"clock={gpu.clock_gr}MHz") + 1
        safe_addnstr(stdscr, row, col2, f"/ {gpu.clock_max_gr}MHz", W - col2, DIM)
    row += 1

    # Per-process VRAM
    if gpu.procs:
        safe_addnstr(stdscr, row, 2, "Processes:", W, WHITE)
        col = 14
        for p in gpu.procs:
            txt = f"{p['name']}={p['mem_mib']}MiB"
            safe_addnstr(stdscr, row, col, txt, W - col, DIM)
            col += len(txt) + 2
        row += 1

    return row


def _draw_service_panel(
    stdscr,
    row: int,
    snap: Snapshot,
    svc_port: int,
    util_hist: UtilHistory,
    sparklines: dict,
    width: int,
    colors: dict,
) -> int:
    """Draw a single service panel. Returns the next row."""
    W = width
    GREEN = colors["GREEN"]
    YELLOW = colors["YELLOW"]
    RED = colors["RED"]
    CYAN = colors["CYAN"]
    WHITE = colors["WHITE"]
    DIM = colors["DIM"]
    BOLD = colors["BOLD"]
    bar_w = 20

    safe_addnstr(stdscr, row, 0, f" {snap.service_name}", W, CYAN | BOLD)
    safe_addnstr(
        stdscr,
        row,
        len(snap.service_name) + 2,
        f"  :{svc_port}",
        W - len(snap.service_name) - 2,
        DIM,
    )
    row += 1

    if not snap.online:
        util_hist.record(svc_port, False)
        safe_addnstr(stdscr, row, 2, "OFFLINE", 7, RED)
        row += 3
        return row

    util_hist.record(svc_port, snap.running > 0)

    # Update sparklines
    key = svc_port
    if key not in sparklines:
        sparklines[key] = {
            "gen_tps": SparklineBuffer(),
            "avg_e2e": SparklineBuffer(),
            "waiting": SparklineBuffer(),
        }
    sparklines[key]["gen_tps"].push(snap.gen_tps)
    sparklines[key]["avg_e2e"].push(snap.avg_e2e)
    sparklines[key]["waiting"].push(snap.waiting)

    # Status + utilization windows
    safe_addnstr(stdscr, row, 2, "UP", 2, GREEN)
    u1 = util_hist.avg(svc_port, 60)
    u5 = util_hist.avg(svc_port, 300)
    u15 = util_hist.avg(svc_port, 900)
    util_str = f"busy: 1m={u1:.0f}%  5m={u5:.0f}%  15m={u15:.0f}%"
    safe_addnstr(stdscr, row, 6, util_str, W - 6, DIM)
    row += 1

    # Requests
    safe_addnstr(stdscr, row, 0, "  Requests:", W, WHITE)
    col = 14
    r_color = GREEN if snap.running == 0 else YELLOW
    txt = f"running={int(snap.running)}"
    safe_addnstr(stdscr, row, col, txt, W - col, r_color)
    col += len(txt) + 2
    w_color = GREEN if snap.waiting == 0 else RED
    txt = f"waiting={int(snap.waiting)}"
    safe_addnstr(stdscr, row, col, txt, W - col, w_color)
    col += len(txt) + 2

    # Queue sparkline
    q_spark = sparklines[key]["waiting"].render()
    if q_spark:
        safe_addnstr(stdscr, row, col, q_spark, W - col, YELLOW)
        col += len(q_spark) + 2

    txt = f"total={int(snap.req_count)}"
    safe_addnstr(stdscr, row, col, txt, W - col, DIM)
    col += len(txt) + 2
    if snap.req_ps > 0:
        txt = f"({snap.req_ps:.1f} req/s)"
        safe_addnstr(stdscr, row, col, txt, W - col, GREEN)
    row += 1

    # KV Cache
    safe_addnstr(stdscr, row, 0, "  KV Cache:", W, WHITE)
    kv_color = GREEN if snap.kv_cache_pct < 50 else YELLOW if snap.kv_cache_pct < 80 else RED
    bar = _draw_bar(snap.kv_cache_pct, 100, bar_w)
    safe_addnstr(stdscr, row, 14, f"{bar} {snap.kv_cache_pct:.1f}%", W - 14, kv_color)
    row += 1

    # Throughput
    safe_addnstr(stdscr, row, 0, "  Throughput:", W, WHITE)
    tp_color = GREEN if snap.gen_tps > 0 else DIM
    txt = f"gen={snap.gen_tps:.1f} tok/s"
    safe_addnstr(stdscr, row, 14, txt, W - 14, tp_color)
    col = 14 + len(txt) + 1
    # Sparkline
    spark = sparklines[key]["gen_tps"].render()
    if spark:
        safe_addnstr(stdscr, row, col, spark, W - col, GREEN)
        col += len(spark) + 2
    else:
        col += 1
    txt = f"prefill={snap.prompt_tps:.1f} tok/s"
    safe_addnstr(stdscr, row, col, txt, W - col, tp_color)
    row += 1

    # Tokens
    safe_addnstr(stdscr, row, 0, "  Tokens:", W, WHITE)
    safe_addnstr(
        stdscr,
        row,
        14,
        f"generated={fmt_tokens(snap.gen_total)}  prompt={fmt_tokens(snap.prompt_total)}",
        W - 14,
        DIM,
    )
    row += 1

    # Latency
    safe_addnstr(stdscr, row, 0, "  Latency:", W, WHITE)
    e2e_color = GREEN if snap.avg_e2e < 5 else YELLOW if snap.avg_e2e < 15 else RED
    txt = f"avg_e2e={snap.avg_e2e:.2f}s  avg_ttft={snap.avg_ttft:.3f}s"
    safe_addnstr(stdscr, row, 14, txt, W - 14, e2e_color)
    col = 14 + len(txt) + 1
    spark = sparklines[key]["avg_e2e"].render()
    if spark:
        safe_addnstr(stdscr, row, col, spark, W - col, YELLOW)
    row += 1

    # Prefix cache & preemptions
    safe_addnstr(stdscr, row, 0, "  Prefix$:", W, WHITE)
    hr = snap.prefix_hit_rate
    hr_color = GREEN if hr > 50 else YELLOW if hr > 20 else DIM
    txt = f"hit_rate={hr:.0f}%"
    safe_addnstr(stdscr, row, 14, txt, W - 14, hr_color)
    col = 14 + len(txt) + 2
    p_color = GREEN if snap.preemptions == 0 else RED
    safe_addnstr(
        stdscr, row, col, f"preemptions={int(snap.preemptions)}", W - col, p_color
    )
    row += 1

    row += 1  # blank line
    return row


def draw(stdscr, config: MonitorConfig, csv_logger: CsvLogger | None = None):
    """Main curses draw loop."""
    curses.curs_set(0)
    curses.use_default_colors()
    curses.start_color()
    curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_YELLOW, -1)
    curses.init_pair(3, curses.COLOR_RED, -1)
    curses.init_pair(4, curses.COLOR_CYAN, -1)
    curses.init_pair(5, curses.COLOR_WHITE, -1)
    curses.init_pair(6, curses.COLOR_MAGENTA, -1)

    colors = {
        "GREEN": curses.color_pair(1) | curses.A_BOLD,
        "YELLOW": curses.color_pair(2) | curses.A_BOLD,
        "RED": curses.color_pair(3) | curses.A_BOLD,
        "CYAN": curses.color_pair(4) | curses.A_BOLD,
        "WHITE": curses.color_pair(5),
        "MAGENTA": curses.color_pair(6) | curses.A_BOLD,
        "BOLD": curses.A_BOLD,
        "DIM": curses.A_DIM,
    }

    collector = SnapshotCollector()
    util_hist = UtilHistory(refresh=config.refresh)
    alert_mgr = AlertManager(config.alerts)
    sparklines: dict = {}

    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        W = width

        # Collect GPU info
        gpus = [] if config.no_gpu else fetch_gpu_info()

        # Header
        header = " vLLM Service Monitor "
        ts = time.strftime("%H:%M:%S")
        safe_addnstr(stdscr, 0, 0, "=" * (W - 1), W - 1, colors["DIM"])
        safe_addnstr(
            stdscr, 0, max(0, (W - len(header)) // 2), header, len(header), colors["BOLD"]
        )
        safe_addnstr(stdscr, 0, max(0, W - len(ts) - 1), ts, len(ts), colors["DIM"])

        row = 2

        # Alert banner
        active_alerts = alert_mgr.active_alerts
        if active_alerts:
            alert_text = " ALERT: " + " | ".join(active_alerts) + " "
            safe_addnstr(
                stdscr,
                row,
                max(0, (W - len(alert_text)) // 2),
                alert_text,
                len(alert_text),
                colors["RED"],
            )
            row += 2

        # GPU panels
        for gpu in gpus:
            row = _draw_gpu_panel(stdscr, row, gpu, W, colors)

        if gpus:
            row += 1

        # Service panels
        for svc in config.services:
            snap = collector.collect(svc)

            # Alerts
            new_alerts = alert_mgr.check(svc.name, snap)
            if new_alerts:
                alert_mgr.fire(new_alerts)

            # CSV logging
            if csv_logger:
                csv_logger.write_row(snap, gpus)

            row = _draw_service_panel(
                stdscr, row, snap, svc.port, util_hist, sparklines, W, colors
            )

        # Footer
        svc_count = len(config.services)
        footer = f" {svc_count} service(s) | Refresh: {config.refresh}s | q=quit "
        try:
            safe_addnstr(
                stdscr, height - 1, 0, "=" * (W - 1), W - 1, colors["DIM"]
            )
            safe_addnstr(
                stdscr,
                height - 1,
                max(0, (W - len(footer)) // 2),
                footer,
                len(footer),
                colors["DIM"],
            )
        except curses.error:
            pass

        stdscr.refresh()

        stdscr.timeout(config.refresh * 1000)
        ch = stdscr.getch()
        if ch in (ord("q"), ord("Q")):
            break
