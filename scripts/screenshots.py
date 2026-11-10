"""Renders the web page with made-up data and takes the screenshots in
docs/screenshots with a headless browser.

    uv run --with playwright==1.65.0 python scripts/screenshots.py

The page is rendered by nvme_web itself, from status files and a history
written here; nothing is read from a real drive. The browser is the installed
Google Chrome, else Playwright's Chromium
(`uv run --with playwright==1.65.0 playwright install chromium` once), both
headless.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs/screenshots"
os.environ["TZ"] = "UTC"
time.tzset()
sys.path.insert(0, str(ROOT / "nvme_monitor/rootfs/usr/local/lib"))

import nvme_web as w  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

NOW = 1_794_052_800  # 2026-11-10 12:00 UTC
DAY, HOUR = 86400, 3600
UNITS_PER_GB = 1e9 / w.UNIT


def version() -> str:
    for line in (ROOT / "nvme_monitor/config.yaml").read_text().splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip('"')
    raise SystemExit("no version in config.yaml")


# Made-up GB written per day, newest last: busier on some days
WRITTEN = [4.2 + 3.1 * ((i * 7) % 5) / 4 + (2.5 if i % 7 == 3 else 0) for i in range(61)]


def smart(day: int, minute: int) -> dict:
    """The SMART log of a made-up 1 TB drive, `day` days before NOW, at
    `minute` minutes after midnight."""
    written = 2400 + sum(WRITTEN[: 60 - day]) + WRITTEN[60 - day] * minute / 1440
    # Warmer in the afternoon, a little noise
    temp = 38 + 3 * math.sin((minute / 1440 - 0.3) * 2 * math.pi) + ((day * 31 + minute) % 7) / 3
    return {
        "critical_warning": 0, "temperature": 273 + round(temp),
        "avail_spare": 100, "spare_thresh": 10, "percent_used": 3,
        "data_units_read": int(written * 1.6 * UNITS_PER_GB), "data_units_written": int(written * UNITS_PER_GB),
        "host_read_commands": int(written * 3_100_000), "host_write_commands": int(written * 1_900_000),
        "controller_busy_time": 3600 - day * 9, "power_cycles": 212, "power_on_hours": 9600 - day * 24,
        "unsafe_shutdowns": 17 if day > 23 else 18, "media_errors": 0, "num_err_log_entries": 0,
        "warning_temp_time": 0, "critical_comp_time": 0,
        "temperature_sensor_1": 273 + round(temp) - 1, "temperature_sensor_2": 273 + round(temp) + 14,
        "thm_temp1_trans_count": 0, "thm_temp2_trans_count": 0, "thm_temp1_total_time": 0, "thm_temp2_total_time": 0,
    }


def build(tmp: Path) -> w.State:
    status_dir = tmp / "status"
    status_dir.mkdir()
    history = w.History(tmp / "history.json")
    # Two months of readings, every 10 minutes, with a week the host was off
    midnight = NOW - 12 * HOUR
    for day in range(60, -1, -1):
        if 34 <= day <= 40:
            continue
        for minute in range(0, 1440, 10):
            at = midnight - day * DAY + minute * 60
            if at <= NOW:
                history.add("nvme0", {"readable": True, "at": at, "smart": smart(day, minute)})
    status = {
        "drive": "nvme0", "name": "Host NVMe", "model": "Example NVMe SSD 1TB", "firmware": "2.1.0",
        "capacity": 1_000_204_886_016, "at": NOW - 20, "read_at": NOW - 20, "failing_since": None,
        "readable": True, "smart": smart(0, 720),
        "thresholds": {"warning_temp_c": 82, "critical_temp_c": 85, "throttle_stage1_c": 80, "throttle_stage2_c": 83},
        "heat_margin": 10, "throttling": None, "reasons": [], "errors": [],
    }
    (status_dir / "nvme0.json").write_text(json.dumps(status))
    state = w.State(history, status_dir, clock=lambda: NOW)
    state.refresh()
    return state


def main() -> None:
    os.environ["APP_VERSION"] = version()
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as name:
        tmp = Path(name)
        state = build(tmp)
        page_file = tmp / "page.html"
        page_file.write_text(w.render(state, "en", "a1b2c3d4_nvme_monitor", now=NOW, every=60))
        with sync_playwright() as p:
            chrome = Path("/Applications/Google Chrome.app").exists() or shutil.which("google-chrome")
            browser = p.chromium.launch(channel="chrome" if chrome else None)
            for scheme, width, height, shots in (
                ("light", 1100, 1000, ("overview", "history")),
                ("dark", 390, 844, ("phone-dark",)),
            ):
                page = browser.new_page(viewport={"width": width, "height": height}, color_scheme=scheme,
                                        device_scale_factor=2 if width < 600 else 1)
                page.goto(page_file.as_uri())
                for shot in shots:
                    path = OUT / f"{shot}.png"
                    if shot == "history":
                        # From the history card to the version line at the bottom
                        top = page.locator(".card", has=page.locator("h2", has_text="History")).bounding_box()["y"]
                        full = page.evaluate("document.documentElement.scrollHeight")
                        page.screenshot(path=path, full_page=True,
                                        clip={"x": 0, "y": top - 16, "width": width, "height": full - top + 16})
                    else:
                        page.screenshot(path=path)
                    print(path.relative_to(ROOT))
                page.close()
            browser.close()


if __name__ == "__main__":
    main()
