"""A small web page behind Home Assistant's ingress, laid out like Home
Assistant's settings: the whole SMART log of every NVMe drive the app reads,
and how it changed.

The monitor service reads the drives and writes one status file per drive to
STATUS_DIR with every reading; Home Assistant only gets the Drive problem
sensor. A drive keeps its counters itself, but no history, so the page keeps
one in HISTORY: the lowest and highest temperature of every hour for HOURS_KEPT
hours, and the counters as they stood at the end of every day for DAYS_KEPT
days. From those it draws the temperature, the data written per day and the
days on which a counter of trouble grew.

The page is rendered on the server and needs nothing beyond Python's standard
library. Its only script takes Home Assistant's theme from the page around
the ingress frame, swaps in a fresh copy of the page every REFRESH seconds
and reloads it when the refresh button is pressed. It changes nothing: the
server answers GET only.

The server runs as its own user without root and without capabilities, so it
cannot reach the drives; it reads the status files and writes its history
only. The app's interval and version come in through the environment.
"""

from __future__ import annotations

import functools
import html
import itertools
import json
import logging
import os
import signal
import socketserver
import sys
import threading
import time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOGGER = logging.getLogger("nvme_web")

PORT = 8099              # the Supervisor's default ingress port
# The Supervisor's ingress proxy; nothing else may talk to the page
INGRESS_PEER = "172.30.32.2"
STATUS_DIR = Path("/run/nvme-monitor")  # written by the monitor service
HISTORY = Path("/data/web/history.json")  # a folder of the page's own user
APP_NAME = "NVMe Monitor"
# The look all the apps' pages share: /usr/local/share/ha-app/ha.css in the
# image, the same place in the repository for the tests
STYLESHEET = Path(__file__).resolve().parents[1] / "share/ha-app/ha.css"
# Home Assistant's language for the page (cookie ha_lang), shared like the
# stylesheet; it runs first, in the page's one script
SHARED_SCRIPT = STYLESHEET.with_name("ha-lang.js")
POLL = 15                # seconds between two reads of the status files
REFRESH = 30             # seconds between two page updates in the browser
SAVE_EVERY = 300         # at most one write of the history per 5 minutes
HOURS_KEPT = 30 * 24
DAYS_KEPT = 366
WRITTEN_DAYS = 90        # days in the data written chart
CHANGES_SHOWN = 20       # rows in the list of days with grown counters
UNIT = 512000            # bytes per data unit of the SMART log
KELVIN = 273
# Counters kept per day; the first four only grow when something went wrong
DAY_KEYS = ("media_errors", "num_err_log_entries", "unsafe_shutdowns", "percent_used",
            "data_units_written", "data_units_read", "power_on_hours")
TROUBLE = DAY_KEYS[:4]
# Bits of the critical warning field (NVMe base specification)
WARNING_BITS = ("spare", "temperature", "reliability", "read_only", "backup", "pmr")

TEXT = {
    "en": {
        "settings": "Settings", "refresh": "Refresh", "as_of": "As of {}",
        "loading": "Waiting for the first reading …", "no_drive": "No readable NVMe drive.",
        "healthy": "The drive is healthy", "problem": "The drive needs attention",
        "unreadable": "SMART log not readable since {}; the values are from the last reading",
        "stale": "No reading for {}: the monitor does not run",
        "reasons": {
            "warning": "Critical warning: {}", "media_errors": "{} media errors",
            "spare": "Spare at {} %, threshold {} %", "worn": "Rated endurance used up ({} %)",
            "heat": "Thermal throttling, {}",
        },
        "drive": "Drive", "model": "Model", "firmware": "Firmware", "capacity": "Capacity",
        "power_on": "Powered on", "power_cycles": "Power cycles", "busy": "Controller busy",
        "read": "Data read", "written": "Data written", "commands": "{} commands",
        "health": "Health", "wear": "Wear", "wear_sub": "of the rated endurance",
        "spare": "Available spare", "spare_sub": "threshold {} %", "media_errors": "Media errors",
        "error_log": "Error log entries", "unsafe": "Unsafe shutdowns", "unsafe_sub": "power lost without a clean shutdown",
        "critical": "Critical warning", "none": "None",
        "bits": {"spare": "spare below threshold", "temperature": "temperature", "reliability": "reliability degraded",
                 "read_only": "read only", "backup": "volatile memory backup failed", "pmr": "persistent memory read only"},
        "temperatures": "Temperatures", "composite": "Composite", "sensor": "Sensor {}",
        "warning_temp": "Warning temperature", "critical_temp": "Critical temperature",
        "stage": "Throttling stage {}", "above_warning": "Time above warning temperature",
        "above_critical": "Time above critical temperature",
        "throttling": "Thermal throttling", "throttle_state": "State", "throttle_heat": "Throttling: {}",
        "throttle_counter": "Counters grow at {} °C without heat", "throttle_none": "No throttling",
        "transitions": "Stage {}", "transitions_sub": "{} transitions",
        "throttle_note": "The counters are the drive's own. Some firmware counts more than thermal throttling in them, "
                         "so they grow at normal temperatures. Throttling counts only when the drive also shows heat: "
                         "a temperature warning, time above its warning or critical temperature, or a composite "
                         "temperature at most {} °C below its lowest threshold.",
        "history": "History", "temp_chart": "Temperature, lowest and highest per hour, last {} days",
        "written_chart": "Data written per day, last {} days",
        "changes": "Days on which a counter of trouble grew",
        "no_changes": "No counter of trouble grew since the history began.",
        "history_empty": "The history begins with the first reading and grows from there.",
        "history_since": "History since {}.", "day": "Day", "change": "Change",
        "gap": "{} – {} · {} in {} days, not read in between",
        "gap_note": "Lighter bars: days without a reading; what was written over them is spread evenly.",
        "never_read": "SMART log not readable since {}",
        "errors": "Error log", "error_count": "Error", "queue": "Queue", "command": "Command",
        "status_field": "Status", "lba": "LBA", "namespace": "Namespace",
        "unknown": "–", "ago": "{} ago", "days": "d", "hours": "h", "minutes": "min", "seconds": "s",
    },
    "de": {
        "settings": "Einstellungen", "refresh": "Aktualisieren", "as_of": "Stand {}",
        "loading": "Warte auf die erste Abfrage …", "no_drive": "Kein lesbares NVMe-Laufwerk.",
        "healthy": "Das Laufwerk ist in Ordnung", "problem": "Das Laufwerk braucht Aufmerksamkeit",
        "unreadable": "SMART-Log seit {} nicht lesbar; die Werte stammen von der letzten Abfrage",
        "stale": "Seit {} keine Abfrage: Der Monitor läuft nicht",
        "reasons": {
            "warning": "Kritische Warnung: {}", "media_errors": "{} Medienfehler",
            "spare": "Reserve bei {} %, Schwelle {} %", "worn": "Nennlebensdauer aufgebraucht ({} %)",
            "heat": "Hitzedrosselung, {}",
        },
        "drive": "Laufwerk", "model": "Modell", "firmware": "Firmware", "capacity": "Kapazität",
        "power_on": "Eingeschaltet", "power_cycles": "Einschaltvorgänge", "busy": "Controller beschäftigt",
        "read": "Gelesen", "written": "Geschrieben", "commands": "{} Befehle",
        "health": "Gesundheit", "wear": "Abnutzung", "wear_sub": "der Nennlebensdauer",
        "spare": "Reserve", "spare_sub": "Schwelle {} %", "media_errors": "Medienfehler",
        "error_log": "Fehlerprotokoll-Einträge", "unsafe": "Unsaubere Abschaltungen",
        "unsafe_sub": "Strom weg ohne sauberes Herunterfahren",
        "critical": "Kritische Warnung", "none": "Keine",
        "bits": {"spare": "Reserve unter Schwelle", "temperature": "Temperatur", "reliability": "Zuverlässigkeit gesunken",
                 "read_only": "nur lesbar", "backup": "Sicherung des flüchtigen Speichers fehlgeschlagen",
                 "pmr": "persistenter Speicher nur lesbar"},
        "temperatures": "Temperaturen", "composite": "Gesamt", "sensor": "Sensor {}",
        "warning_temp": "Warntemperatur", "critical_temp": "Kritische Temperatur",
        "stage": "Drosselstufe {}", "above_warning": "Zeit über Warntemperatur",
        "above_critical": "Zeit über kritischer Temperatur",
        "throttling": "Hitzedrosselung", "throttle_state": "Zustand", "throttle_heat": "Drosselt: {}",
        "throttle_counter": "Zähler steigen bei {} °C ohne Hitze", "throttle_none": "Keine Drosselung",
        "transitions": "Stufe {}", "transitions_sub": "{} Übergänge",
        "throttle_note": "Die Zähler führt das Laufwerk selbst. Manche Firmware zählt darin mehr als die Hitzedrosselung, "
                         "sodass sie bei normalen Temperaturen steigen. Als Drosselung zählt es nur, wenn das Laufwerk "
                         "auch Hitze zeigt: eine Temperaturwarnung, Zeit über Warn- oder kritischer Temperatur oder eine "
                         "Gesamttemperatur höchstens {} °C unter seiner niedrigsten Schwelle.",
        "history": "Verlauf", "temp_chart": "Temperatur, niedrigster und höchster Wert je Stunde, letzte {} Tage",
        "written_chart": "Geschrieben pro Tag, letzte {} Tage",
        "changes": "Tage, an denen ein Störungszähler gestiegen ist",
        "no_changes": "Seit Beginn des Verlaufs ist kein Störungszähler gestiegen.",
        "history_empty": "Der Verlauf beginnt mit der ersten Abfrage und wächst ab dann.",
        "history_since": "Verlauf seit {}.", "day": "Tag", "change": "Änderung",
        "gap": "{} – {} · {} in {} Tagen, dazwischen nicht gelesen",
        "gap_note": "Hellere Balken: Tage ohne Abfrage; was in der Zeit geschrieben wurde, ist gleichmäßig verteilt.",
        "never_read": "SMART-Log seit {} nicht lesbar",
        "errors": "Fehlerprotokoll", "error_count": "Fehler", "queue": "Queue", "command": "Befehl",
        "status_field": "Status", "lba": "LBA", "namespace": "Namespace",
        "unknown": "–", "ago": "vor {}", "days": "d", "hours": "h", "minutes": "min", "seconds": "s",
    },
}

# Material Design Icons (Apache 2.0), as Home Assistant draws them
ICONS = {
    "settings": "M12,8A4,4 0 0,1 16,12A4,4 0 0,1 12,16A4,4 0 0,1 8,12A4,4 0 0,1 12,8M12,10A2,2 0 0,0 10,12A2,2 0 0,0 12,14A2,2 0 0,0 14,12A2,2 0 0,0 12,10M10,22C9.75,22 9.54,21.82 9.5,21.58L9.13,18.93C8.5,18.68 7.96,18.34 7.44,17.94L4.95,18.95C4.73,19.03 4.46,18.95 4.34,18.73L2.34,15.27C2.21,15.05 2.27,14.78 2.46,14.63L4.57,12.97L4.5,12L4.57,11L2.46,9.37C2.27,9.22 2.21,8.95 2.34,8.73L4.34,5.27C4.46,5.05 4.73,4.96 4.95,5.05L7.44,6.05C7.96,5.66 8.5,5.32 9.13,5.07L9.5,2.42C9.54,2.18 9.75,2 10,2H14C14.25,2 14.46,2.18 14.5,2.42L14.87,5.07C15.5,5.32 16.04,5.66 16.56,6.05L19.05,5.05C19.27,4.96 19.54,5.05 19.66,5.27L21.66,8.73C21.79,8.95 21.73,9.22 21.54,9.37L19.43,11L19.5,12L19.43,13L21.54,14.63C21.73,14.78 21.79,15.05 21.66,15.27L19.66,18.73C19.54,18.95 19.27,19.04 19.05,18.95L16.56,17.95C16.04,18.34 15.5,18.68 14.87,18.93L14.5,21.58C14.46,21.82 14.25,22 14,22H10M11.25,4L10.88,6.61C9.68,6.86 8.62,7.5 7.85,8.39L5.44,7.35L4.69,8.65L6.8,10.2C6.4,11.37 6.4,12.64 6.8,13.8L4.68,15.36L5.43,16.66L7.86,15.62C8.63,16.5 9.68,17.14 10.87,17.38L11.24,20H12.76L13.13,17.39C14.32,17.14 15.37,16.5 16.14,15.62L18.57,16.66L19.32,15.36L17.2,13.81C17.6,12.64 17.6,11.37 17.2,10.2L19.31,8.65L18.56,7.35L16.15,8.39C15.38,7.5 14.32,6.86 13.12,6.62L12.75,4H11.25Z",
    "ok": "M12 2C6.5 2 2 6.5 2 12S6.5 22 12 22 22 17.5 22 12 17.5 2 12 2M10 17L5 12L6.41 10.59L10 14.17L17.59 6.58L19 8L10 17Z",
    "alert": "M13,13H11V7H13M13,17H11V15H13M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2Z",
    "refresh": "M17.65,6.35C16.2,4.9 14.21,4 12,4A8,8 0 0,0 4,12A8,8 0 0,0 12,20C15.73,20 18.84,17.45 19.73,14H17.65C16.83,16.33 14.61,18 12,18A6,6 0 0,1 6,12A6,6 0 0,1 12,6C13.66,6 15.14,6.69 16.22,7.78L13,11H20V4L17.65,6.35Z",
}


def icon(name: str, cls: str = "") -> str:
    return f'<svg class="{cls}" viewBox="0 0 24 24" aria-hidden="true"><path d="{ICONS[name]}"/></svg>'


@functools.cache
def stylesheet() -> str:
    """The stylesheet all the apps share (ha.css), then this page's own rules."""
    try:
        return STYLESHEET.read_text() + APP_STYLE
    except OSError as err:
        LOGGER.warning("Cannot read the stylesheet: %s", err)
        return APP_STYLE


APP_STYLE = """
section > h2 { margin: 8px 4px 16px; font-size: var(--ha-font-size-xl); font-weight: var(--ha-font-weight-normal); }
.card h3 { margin: 16px 0 8px; font-size: var(--ha-font-size-m); font-weight: var(--ha-font-weight-medium); }
ul.reasons { margin: 0 0 8px; padding-left: 32px; }
.content .note { padding: 8px 0 0; }
svg.chart { display: block; width: 100%; min-width: 640px; height: auto; margin-bottom: 8px; }
svg.chart .grid { stroke: var(--divider-color); stroke-width: 1; }
svg.chart .axis { fill: var(--secondary-text-color); font-size: 11px; }
svg.chart .band { fill: var(--primary-color); fill-opacity: .25; }
svg.chart .bar { fill: var(--primary-color); }
svg.chart .bar.gap { fill-opacity: .4; }
svg.chart .hit { fill: transparent; }
svg.chart .hit:hover { fill: var(--ha-hover); }
"""
# Takes Home Assistant's theme from the page around the ingress frame (same
# origin) and follows it; updates the page by itself and reloads it on the
# refresh button
THEME_VARS = ("primary-background-color", "secondary-background-color", "card-background-color",
              "primary-text-color", "secondary-text-color", "divider-color", "primary-color", "text-primary-color",
              "success-color", "warning-color", "error-color", "info-color", "disabled-text-color",
              "sidebar-background-color", "sidebar-text-color", "sidebar-icon-color", "app-header-border-bottom",
              "ha-card-border-radius", "ha-card-border-width", "ha-card-border-color", "ha-card-box-shadow",
              "ha-font-family-body", "header-height")
SCRIPT = """(() => {
  try {
    const outer = window.parent.document.documentElement;
    if (outer !== document.documentElement) {
      const take = () => {
        const theme = getComputedStyle(outer), own = document.documentElement.style;
        for (const n of NAMES) { const v = theme.getPropertyValue("--" + n).trim(); if (v) own.setProperty("--" + n, v); }
        const ha = window.parent.document.querySelector("home-assistant");
        if (ha && ha.hass) own.colorScheme = ha.hass.themes.darkMode ? "dark" : "light";
      };
      take();
      new MutationObserver(take).observe(outer, { attributes: true, attributeFilter: ["style", "class"] });
    }
  } catch (e) {}
  // Swaps in a fresh copy of the page's content and its stamp
  let busy = false;
  const update = async () => {
    if (busy) return;
    busy = true;
    try {
      const page = await fetch(location.href, { cache: "no-store" });
      if (!page.ok) return;
      const doc = new DOMParser().parseFromString(await page.text(), "text/html");
      document.querySelector("main").replaceWith(doc.querySelector("main"));
      const stamp = document.querySelector("header .stamp"), fresh = doc.querySelector("header .stamp");
      if (stamp && fresh) stamp.replaceWith(fresh);
    } catch (e) {
    } finally {
      busy = false;
    }
  };
  document.addEventListener("click", (e) => {
    if (e.target.closest("#refresh")) { e.preventDefault(); location.reload(); }
  });
  setInterval(() => { if (!document.hidden) update(); }, REFRESH * 1000);
})();""".replace("NAMES", json.dumps(THEME_VARS)).replace("REFRESH", str(REFRESH))


def shared_script() -> str:
    try:
        return SHARED_SCRIPT.read_text()
    except OSError as err:
        LOGGER.warning("Cannot read the language script: %s", err)
        return ""


SCRIPT = shared_script() + SCRIPT


# --- Status files and history -------------------------------------------------------
def read_status(directory: Path) -> dict[str, dict]:
    """The latest status file of every drive, by its name (nvme0, …)."""
    drives = {}
    for path in sorted(directory.glob("nvme*.json")):
        try:
            status = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(status, dict) and isinstance(status.get("smart"), dict):
            drives[path.stem] = status
    return drives


def celsius(kelvin) -> int | None:
    return kelvin - KELVIN if isinstance(kelvin, (int, float)) and kelvin > 0 else None


def local_day(epoch: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(epoch))


def is_day(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return len(value) == 10


def valid_entry(entry) -> tuple[dict | None, int]:
    """The parts of a stored history entry that have the expected form, and
    how many parts were left out. A file cut short or edited by hand must not
    stop the history from growing."""
    if not (isinstance(entry, dict) and isinstance(entry.get("hours"), dict) and isinstance(entry.get("days"), dict)):
        return None, 1
    hours = {hour: [low, high] for hour, value in entry["hours"].items()
             if isinstance(hour, str) and hour.isdigit() and isinstance(value, list) and len(value) == 2
             for low, high in [value]
             if all(isinstance(x, int | float) and not isinstance(x, bool) for x in (low, high))}
    days = {day: {key: n for key, n in value.items()
                  if key in DAY_KEYS and isinstance(n, int) and not isinstance(n, bool)}
            for day, value in entry["days"].items() if is_day(day) and isinstance(value, dict)}
    lost = len(entry["hours"]) - len(hours) + len(entry["days"]) - len(days)
    return {"hours": hours, "days": days}, lost


class History:
    """Per drive: the lowest and highest temperature of every hour, and the
    counters as they stood at the end of every day."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, dict] = {}
        self._saved = 0.0
        try:
            stored = json.loads(path.read_text())
        except (OSError, ValueError):
            return
        drives = stored.get("drives") if isinstance(stored, dict) else None
        dropped = 0
        for drive, entry in (drives.items() if isinstance(drives, dict) else ()):
            clean, lost = valid_entry(entry)
            dropped += lost
            if clean is not None:
                self.data[drive] = clean
        if dropped or not isinstance(drives, dict):
            LOGGER.warning("Left out %s of the history in %s: not in the expected form",
                           f"{dropped} entries" if isinstance(drives, dict) else "everything", path)

    def add(self, drive: str, status: dict) -> None:
        """Takes one reading; readings of an unreadable drive are left out."""
        if not status.get("readable"):
            return
        smart, at = status["smart"], status["at"]
        entry = self.data.setdefault(drive, {"hours": {}, "days": {}})
        temp = celsius(smart.get("temperature"))
        if temp is not None:
            hour = str(int(at) // 3600 * 3600)
            low, high = entry["hours"].get(hour, (temp, temp))
            entry["hours"][hour] = [min(low, temp), max(high, temp)]
        entry["days"][local_day(at)] = {key: smart[key] for key in DAY_KEYS if isinstance(smart.get(key), int)}
        for key, keep in (("hours", HOURS_KEPT), ("days", DAYS_KEPT)):
            for old in sorted(entry[key])[:-keep]:
                del entry[key][old]

    def save(self, now: float) -> None:
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps({"drives": self.data}, sort_keys=True))
            os.replace(tmp, self.path)
            self._saved = now
        except OSError as err:
            LOGGER.warning("Cannot save %s: %s", self.path, err)

    def maybe_save(self, now: float) -> None:
        if now - self._saved >= SAVE_EVERY:
            self.save(now)


def problems(status: dict) -> list[tuple[str, tuple]]:
    """The same rule as the Drive problem sensor, as text keys and values."""
    s = status["smart"]
    found: list[tuple[str, tuple]] = []
    if s.get("critical_warning"):
        found.append(("warning", (s["critical_warning"],)))
    if s.get("media_errors"):
        found.append(("media_errors", (s["media_errors"],)))
    if s.get("avail_spare") is not None and s.get("spare_thresh") is not None and s["avail_spare"] <= s["spare_thresh"]:
        found.append(("spare", (s["avail_spare"], s["spare_thresh"])))
    if (s.get("percent_used") or 0) >= 100:
        found.append(("worn", (s["percent_used"],)))
    throttling = status.get("throttling") or {}
    if throttling.get("kind") == "heat":
        found.append(("heat", (throttling.get("detail", ""),)))
    return found


def interval() -> int:
    """Seconds between two readings, from the app's options (web/run passes
    the option on, since the page's user cannot read the options file)."""
    try:
        return max(int(os.environ.get("NVME_INTERVAL", "")), 1)
    except ValueError:
        return 60


def version() -> str:
    """The app's version, as the Supervisor built the image with it."""
    return os.environ.get("APP_VERSION", "").strip()


class State:
    """The status files as last read, and the history built from them."""

    def __init__(self, history: History, directory: Path = STATUS_DIR, clock=time.time) -> None:
        self.history = history
        self.directory = directory
        self._clock = clock
        self.lock = threading.Lock()
        self.drives: dict[str, dict] = {}
        self.at = 0.0
        self._taken: dict[str, float] = {}

    def refresh(self) -> None:
        drives = read_status(self.directory)
        now = self._clock()
        with self.lock:
            self.drives, self.at = drives, now
            for drive, status in drives.items():
                # Every reading once, also when the monitor stopped writing
                if self._taken.get(drive) != status.get("at"):
                    self._taken[drive] = status.get("at")
                    self.history.add(drive, status)
            self.history.maybe_save(now)

    def run(self) -> None:
        while True:
            try:
                self.refresh()
            except Exception:  # noqa: BLE001 (the loop must keep going)
                LOGGER.exception("Reading the status files failed")
            time.sleep(POLL)


# --- Formatting --------------------------------------------------------------------------
def lang_of(cookie: str | None, accept: str | None = None) -> str:
    """German or English, as Home Assistant's interface shows it.

    The page script keeps Home Assistant's language in the cookie ha_lang
    (share/ha-app/ha-lang.js): German for "de", English for everything else.
    Only without that cookie, German if the browser puts German before English.
    """
    for part in (cookie or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == "ha_lang":
            return "de" if value.strip().lower() == "de" else "en"
    for part in (accept or "").split(","):
        code = part.split(";")[0].strip().lower()
        if code.startswith("de"):
            return "de"
        if code.startswith("en"):
            return "en"
    return "en"


class Format:
    def __init__(self, lang: str) -> None:
        self.lang = lang
        self.t = TEXT[lang]

    def dec(self, value: float, digits: int) -> str:
        text = f"{value:,.{digits}f}".replace("-", "−")
        if self.lang == "de":
            text = text.replace(",", " ").replace(".", ",").replace(" ", ".")
        return text

    def int(self, value: int) -> str:
        text = f"{value:,}"
        return text.replace(",", ".") if self.lang == "de" else text

    def size(self, num_bytes: float) -> str:
        """Decimal units, as drives are sold: 3,86 TB, 256 GB, 412 MB."""
        for unit, factor in (("TB", 1e12), ("GB", 1e9), ("MB", 1e6)):
            if num_bytes >= factor:
                value = num_bytes / factor
                return f"{self.dec(value, 2 if value < 10 else 1 if value < 100 else 0)} {unit}"
        return f"{self.int(round(num_bytes / 1e3))} kB"

    def duration(self, seconds: float) -> str:
        """Two units at most: 45 s, 12 min, 3 h 5 min, 2 d 4 h."""
        t = self.t
        s = round(seconds)
        if s < 60:
            return f"{s} {t['seconds']}"
        if s < 3600:
            return f"{s // 60} {t['minutes']}"
        if s < 86400:
            h, m = divmod(s // 60, 60)
            return f"{h} {t['hours']} {m} {t['minutes']}" if m else f"{h} {t['hours']}"
        d, h = divmod(s // 3600, 24)
        return f"{d} {t['days']} {h} {t['hours']}" if h else f"{d} {t['days']}"

    def when(self, epoch: float) -> str:
        return time.strftime("%d.%m.%Y %H:%M" if self.lang == "de" else "%Y-%m-%d %H:%M", time.localtime(epoch))

    def day(self, iso: str) -> str:
        y, m, d = iso.split("-")
        return f"{d}.{m}.{y}" if self.lang == "de" else iso

    def short_day(self, iso: str) -> str:
        _, m, d = iso.split("-")
        return f"{d}.{m}." if self.lang == "de" else f"{m}-{d}"

    def temp(self, value) -> str:
        return self.t["unknown"] if value is None else f"{value} °C"


# --- The page ------------------------------------------------------------------------------
def esc(value) -> str:
    return html.escape(str(value), quote=True)


def kv(rows: list[tuple[str, str]]) -> str:
    return '<table class="kv">' + "".join(f"<tr><th>{esc(k)}</th><td>{v}</td></tr>" for k, v in rows) + "</table>"


def card(title: str | None, content: str, padded: bool = True) -> str:
    head = f"<h2>{esc(title)}</h2>" if title else ""
    inner = f'<div class="content">{content}</div>' if padded else content
    return f'<div class="card">{head}{inner}</div>'


def sub(main: str, below: str) -> str:
    return f'{main}<span class="sub">{esc(below)}</span>'


def warning_bits(f: Format, value: int) -> str:
    bits = [f.t["bits"][name] for i, name in enumerate(WARNING_BITS) if value >> i & 1]
    return ", ".join(bits) or f"0x{value:02x}"


def status_card(f: Format, status: dict, now: float, every: int) -> str:
    t = f.t
    found = problems(status)
    if found:
        texts = [t["reasons"][key].format(*(warning_bits(f, v[0]),) if key == "warning" else v) for key, v in found]
        state = (f'<div class="state bad">{icon("alert")}{esc(t["problem"])}</div>'
                 f'<ul class="reasons">{"".join(f"<li>{esc(x)}</li>" for x in texts)}</ul>')
    elif status["smart"]:
        state = f'<div class="state ok">{icon("ok")}{esc(t["healthy"])}</div>'
    else:
        # Never read: nothing is known about its health
        state = ""
    if not status.get("readable"):
        since = f.when(status.get("failing_since") or status["at"])
        text = t["unreadable" if status["smart"] else "never_read"].format(since)
        state += f'<div class="state bad">{icon("alert")}{esc(text)}</div>'
    # Apart from the above: the monitor writes the file every round, readable or not
    if now - status["at"] > 3 * every:
        state += f'<div class="state bad">{icon("alert")}{esc(t["stale"].format(f.duration(now - status["at"])))}</div>'
    s = status["smart"]
    rows = [(t["model"], esc(status.get("model") or t["unknown"])),
            (t["firmware"], esc(status.get("firmware") or t["unknown"]))]
    if status.get("capacity"):
        rows.append((t["capacity"], esc(f.size(status["capacity"]))))
    if s.get("power_on_hours") is not None:
        rows.append((t["power_on"], esc(f.duration(s["power_on_hours"] * 3600))))
    if s.get("power_cycles") is not None:
        rows.append((t["power_cycles"], esc(f.int(s["power_cycles"]))))
    if s.get("controller_busy_time") is not None:
        rows.append((t["busy"], esc(f.duration(s["controller_busy_time"] * 60))))
    for key, units, commands in (("read", "data_units_read", "host_read_commands"),
                                 ("written", "data_units_written", "host_write_commands")):
        if s.get(units) is not None:
            value = esc(f.size(s[units] * UNIT))
            rows.append((t[key], sub(value, t["commands"].format(f.int(s[commands])))
                         if s.get(commands) is not None else value))
    return card(t["drive"], state + kv(rows))


def health_card(f: Format, status: dict) -> str:
    t, s = f.t, status["smart"]
    rows = []
    if s.get("percent_used") is not None:
        rows.append((t["wear"], sub(esc(f"{s['percent_used']} %"), t["wear_sub"])))
    if s.get("avail_spare") is not None:
        rows.append((t["spare"], sub(esc(f"{s['avail_spare']} %"), t["spare_sub"].format(s.get("spare_thresh", "–")))))
    for key, field in (("media_errors", "media_errors"), ("error_log", "num_err_log_entries")):
        if s.get(field) is not None:
            rows.append((t[key], esc(f.int(s[field]))))
    if s.get("unsafe_shutdowns") is not None:
        rows.append((t["unsafe"], sub(esc(f.int(s["unsafe_shutdowns"])), t["unsafe_sub"])))
    warning = s.get("critical_warning") or 0
    rows.append((t["critical"], f'<span class="bad">{esc(warning_bits(f, warning))}</span>' if warning
                 else esc(t["none"])))
    return card(t["health"], kv(rows))


def temperature_card(f: Format, status: dict) -> str:
    t, s, th = f.t, status["smart"], status.get("thresholds") or {}
    rows = [(t["composite"], esc(f.temp(celsius(s.get("temperature")))))]
    for i in range(1, 9):
        value = celsius(s.get(f"temperature_sensor_{i}"))
        if value is not None:
            rows.append((t["sensor"].format(i), esc(f.temp(value))))
    for key, field in (("warning_temp", "warning_temp_c"), ("critical_temp", "critical_temp_c")):
        if th.get(field) is not None:
            rows.append((t[key], esc(f.temp(th[field]))))
    for stage in (1, 2):
        if th.get(f"throttle_stage{stage}_c") is not None:
            rows.append((t["stage"].format(stage), esc(f.temp(th[f"throttle_stage{stage}_c"]))))
    # Both in minutes
    for key, field in (("above_warning", "warning_temp_time"), ("above_critical", "critical_comp_time")):
        if s.get(field) is not None:
            rows.append((t[key], esc(f.duration(s[field] * 60))))
    return card(t["temperatures"], kv(rows))


def throttling_card(f: Format, status: dict) -> str:
    t, s = f.t, status["smart"]
    throttling = status.get("throttling") or {}
    if throttling.get("kind") == "heat":
        state = f'<span class="bad">{esc(t["throttle_heat"].format(throttling.get("detail", "")))}</span>'
    elif throttling.get("kind") == "counter":
        state = esc(t["throttle_counter"].format(throttling.get("detail", "")))
    else:
        state = esc(t["throttle_none"])
    rows = [(t["throttle_state"], state)]
    for stage in (1, 2):
        count, total = s.get(f"thm_temp{stage}_trans_count"), s.get(f"thm_temp{stage}_total_time")
        if count is not None:
            rows.append((t["transitions"].format(stage),
                         sub(esc(f.duration(total or 0)), t["transitions_sub"].format(f.int(count)))))
    note = f'<div class="note">{esc(t["throttle_note"].format(status.get("heat_margin", 10)))}</div>'
    return card(t["throttling"], kv(rows) + note)


# --- Charts ---------------------------------------------------------------------------------
CHART_W, CHART_H = 1000, 200
PAD_L, PAD_R, PAD_T, PAD_B = 60, 8, 12, 24


def ticks(low: float, high: float, count: int = 4) -> list[float]:
    """Round values for the y axis between low and high."""
    span = max(high - low, 1e-9)
    raw = span / count
    # The round step closest to the raw one, by ratio
    step = min((m * 10 ** e for e in range(-3, 16) for m in (1, 2, 5)),
               key=lambda s: max(s / raw, raw / s))
    first = -(-low // step) * step
    values, v = [], first
    while v <= high + 1e-9:
        values.append(round(v, 6))
        v += step
    return values


def svg_chart(f: Format, body: str, y_ticks: list[float], y_of, y_label, x_labels: list[tuple[float, str]],
              title: str) -> str:
    grid = "".join(f'<line class="grid" x1="{PAD_L}" x2="{CHART_W - PAD_R}" y1="{y_of(v):.1f}" y2="{y_of(v):.1f}"/>'
                   f'<text class="axis" x="{PAD_L - 6}" y="{y_of(v) + 4:.1f}" text-anchor="end">{esc(y_label(v))}</text>'
                   for v in y_ticks)
    xs = "".join(f'<text class="axis" x="{x:.1f}" y="{CHART_H - 6}" text-anchor="middle">{esc(text)}</text>'
                 for x, text in x_labels)
    # Scrolls sideways on a phone instead of shrinking the axis text
    return (f'<div class="scroll"><svg class="chart" viewBox="0 0 {CHART_W} {CHART_H}" role="img"'
            f' aria-label="{esc(title)}">{grid}{body}{xs}</svg></div>')


def temperature_chart(f: Format, hours: dict[str, list[int]], now: float) -> str:
    days = HOURS_KEPT // 24
    end = int(now) // 3600 * 3600 + 3600
    start = end - HOURS_KEPT * 3600
    points = sorted((int(h), v) for h, v in hours.items() if int(h) >= start)
    if not points:
        return ""
    low = min(v[0] for _, v in points) - 2
    high = max(v[1] for _, v in points) + 2
    plot_w, plot_h = CHART_W - PAD_L - PAD_R, CHART_H - PAD_T - PAD_B
    step = plot_w / HOURS_KEPT

    def x_of(hour: int) -> float:
        return PAD_L + (hour - start) / 3600 * step

    def y_of(value: float) -> float:
        return PAD_T + (high - value) / (high - low) * plot_h

    # The band runs through consecutive hours; a missing hour breaks it
    runs, run = [], []
    for hour, value in points:
        if run and hour - run[-1][0] > 3600:
            runs.append(run)
            run = []
        run.append((hour, value))
    runs.append(run)
    paths = []
    for run in runs:
        top = [(x_of(h), y_of(v[1])) for h, v in run] + [(x_of(run[-1][0]) + step, y_of(run[-1][1][1]))]
        bottom = [(x_of(h), y_of(v[0])) for h, v in run] + [(x_of(run[-1][0]) + step, y_of(run[-1][1][0]))]
        # At least 2 px high, so a steady temperature still shows
        bottom = [(x, max(y, ty + 2)) for (x, y), (_, ty) in zip(bottom, top, strict=True)]
        d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in top + bottom[::-1]) + "Z"
        paths.append(f'<path class="band" d="{d}"/>')
    hits = "".join(f'<rect class="hit" x="{x_of(h):.1f}" y="{PAD_T}" width="{max(step, 1):.2f}" height="{plot_h}">'
                   f'<title>{esc(f.when(h))} · {v[0]}–{v[1]} °C</title></rect>' for h, v in points)
    labels = []
    for i in range(0, days + 1, 7):
        hour = start + i * 86400
        labels.append((x_of(hour), f.short_day(local_day(hour))))
    chart = svg_chart(f, "".join(paths) + hits, ticks(low, high), y_of, lambda v: f"{v:g} °C", labels,
                      f.t["temp_chart"].format(days))
    return f'<h3>{esc(f.t["temp_chart"].format(days))}</h3>{chart}'


def spans(order: list[str]) -> list[tuple[str, str, str]]:
    """Each pair of days with a reading as (first day, last day, day before):
    the days the change between the two readings belongs to. Without a gap
    first and last are the same day; after a gap (the app stopped, the host
    was off) the change belongs to all the days of the gap, not to the first
    day after it."""
    return [((date.fromisoformat(before) + timedelta(days=1)).isoformat(), day, before)
            for before, day in itertools.pairwise(order)]


def written_chart(f: Format, days: dict[str, dict]) -> str:
    order = sorted(day for day in days if "data_units_written" in days[day])
    if len(order) < 2:
        return ""
    end = date.fromisoformat(order[-1])
    start = end - timedelta(days=WRITTEN_DAYS - 1)
    # Per calendar day of the chart: bytes written, and the gap it belongs to
    slots: dict[int, tuple[float, tuple[str, str, float, int] | None]] = {}
    for first, last, before in spans(order):
        grown = max(days[last]["data_units_written"] - days[before]["data_units_written"], 0) * UNIT
        length = (date.fromisoformat(last) - date.fromisoformat(first)).days + 1
        for i in range(length):
            index = (date.fromisoformat(first) + timedelta(days=i) - start).days
            if 0 <= index < WRITTEN_DAYS:
                slots[index] = (grown / length, (first, last, grown, length) if length > 1 else None)
    if not slots:
        return ""
    plot_w, plot_h = CHART_W - PAD_L - PAD_R, CHART_H - PAD_T - PAD_B
    slot = plot_w / WRITTEN_DAYS
    width = min(slot - 2, 24)
    high = max(v for v, _ in slots.values()) or 1
    y_ticks = ticks(0, high)
    top = max(y_ticks[-1], high)

    def y_of(value: float) -> float:
        return PAD_T + (top - value) / top * plot_h

    bars, labels = [], []
    for index in range(WRITTEN_DAYS):
        day = (start + timedelta(days=index)).isoformat()
        x = PAD_L + index * slot + (slot - width) / 2
        if index % 14 == 0:
            labels.append((x + width / 2, f.short_day(day)))
        if index not in slots:
            continue
        value, gap = slots[index]
        y, base = y_of(value), PAD_T + plot_h
        r = min(4, width / 2, base - y)
        # Rounded at the data end, square at the baseline
        d = (f"M{x:.1f},{base:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} H{x + width - r:.1f} "
             f"Q{x + width:.1f},{y:.1f} {x + width:.1f},{y + r:.1f} V{base:.1f}Z")
        if value > 0:
            bars.append(f'<path class="bar{" gap" if gap else ""}" d="{d}"/>')
        title = (f.t["gap"].format(f.day(gap[0]), f.day(gap[1]), f.size(gap[2]), gap[3]) if gap
                 else f"{f.day(day)} · {f.size(value)}")
        bars.append(f'<rect class="hit" x="{PAD_L + index * slot:.1f}" y="{PAD_T}" width="{slot:.2f}" '
                    f'height="{plot_h}"><title>{esc(title)}</title></rect>')
    # One unit for the whole axis, decimals only where a tick needs them
    unit, factor = next(((u, x) for u, x in (("TB", 1e12), ("GB", 1e9), ("MB", 1e6)) if top >= x), ("kB", 1e3))
    digits = 0 if all(float(v / factor).is_integer() for v in y_ticks) else 1

    def axis(v: float) -> str:
        return f"{f.dec(v / factor, digits)} {unit}" if v else "0"

    chart = svg_chart(f, "".join(bars), y_ticks, y_of, axis, labels,
                      f.t["written_chart"].format(WRITTEN_DAYS))
    note = (f'<div class="note">{esc(f.t["gap_note"])}</div>'
            if any(gap for _, gap in slots.values()) else "")
    return f'<h3>{esc(f.t["written_chart"].format(WRITTEN_DAYS))}</h3>{chart}{note}'


def changes(days: dict[str, dict]) -> list[tuple[str, str, list[tuple[str, int, int]]]]:
    """Days on which a counter of trouble grew, newest first, as first and
    last day: after a gap in the readings the counter grew somewhen in it."""
    found = []
    for first, day, before in spans(sorted(days)):
        grown = [(key, days[before][key], days[day][key]) for key in TROUBLE
                 if key in days[day] and key in days[before] and days[day][key] > days[before][key]]
        if grown:
            found.append((first, day, grown))
    return found[::-1]


def history_card(f: Format, entry: dict | None, now: float) -> str:
    t = f.t
    if not entry or not entry.get("days"):
        return card(t["history"], f'<p class="muted">{esc(t["history_empty"])}</p>')
    days = entry["days"]
    labels = {"media_errors": t["media_errors"], "num_err_log_entries": t["error_log"],
              "unsafe_shutdowns": t["unsafe"], "percent_used": t["wear"]}
    rows = "".join(
        f'<tr><td class="nowrap">{esc(f.day(day) if first == day else f"{f.day(first)} – {f.day(day)}")}</td><td>'
        + "<br>".join(esc(f"{labels[key]}: {f.int(old)} → {f.int(new)}") for key, old, new in grown) + "</td></tr>"
        for first, day, grown in changes(days)[:CHANGES_SHOWN])
    table = (f'<h3>{esc(t["changes"])}</h3><table class="list"><thead><tr><th>{esc(t["day"])}</th>'
             f'<th>{esc(t["change"])}</th></tr></thead><tbody>{rows}</tbody></table>' if rows
             else f'<h3>{esc(t["changes"])}</h3><p class="muted">{esc(t["no_changes"])}</p>')
    note = f'<div class="note">{esc(t["history_since"].format(f.day(min(days))))}</div>'
    return card(t["history"], temperature_chart(f, entry.get("hours", {}), now) + written_chart(f, days)
                + table + note)


def errors_card(f: Format, status: dict) -> str:
    t, errors = f.t, status.get("errors") or []
    if not errors:
        return ""
    rows = "".join(f'<tr><td class="n">{esc(f.int(e.get("error_count", 0)))}</td><td class="n">{esc(e.get("sqid", ""))}</td>'
                   f'<td class="n">{esc(e.get("cmdid", ""))}</td><td class="n">0x{int(e.get("status_field", 0)):04x}</td>'
                   f'<td class="n opt">{esc(e.get("lba", ""))}</td><td class="n opt">{esc(e.get("nsid", ""))}</td></tr>'
                   for e in errors if isinstance(e, dict))
    head = (f'<thead><tr><th class="n">{esc(t["error_count"])}</th><th class="n">{esc(t["queue"])}</th>'
            f'<th class="n">{esc(t["command"])}</th><th class="n">{esc(t["status_field"])}</th>'
            f'<th class="n opt">{esc(t["lba"])}</th><th class="n opt">{esc(t["namespace"])}</th></tr></thead>')
    return card(t["errors"], f'<div class="scroll"><table class="list">{head}<tbody>{rows}</tbody></table></div>',
                padded=False)


def drive_section(f: Format, status: dict, entry: dict | None, now: float, every: int, titled: bool) -> str:
    head = f"<h2>{esc(status.get('name') or status.get('drive'))}</h2>" if titled else ""
    if not status["smart"]:
        # Never read since the app started: no values to show
        return f'<section>{head}<div class="cards"><div>{status_card(f, status, now, every)}</div></div></section>'
    return (f"<section>{head}<div class=\"cards\"><div>{status_card(f, status, now, every)}{throttling_card(f, status)}</div>"
            f"<div>{health_card(f, status)}{temperature_card(f, status)}</div></div>"
            f"{history_card(f, entry, now)}{errors_card(f, status)}</section>")


def render(state: State, lang: str, slug: str | None, now: float | None = None, every: int | None = None) -> str:
    f = Format(lang)
    t = f.t
    now = state._clock() if now is None else now
    every = interval() if every is None else every
    with state.lock:
        drives = dict(state.drives)
        if drives:
            body = "".join(drive_section(f, status, state.history.data.get(drive), now, every, len(drives) > 1)
                           for drive, status in drives.items())
        else:
            body = card(None, f'<p class="muted">{esc(t["loading"] if not state.at else t["no_drive"])}</p>')
    # The same header as every app's page: name, then stamp, refresh, settings
    stamp = time.strftime("%H:%M:%S", time.localtime(now))
    actions = f'<span class="stamp opt">{esc(t["as_of"].format(stamp))}</span>'
    actions += (f'<a href="" id="refresh" class="icon-button" title="{esc(t["refresh"])}" '
                f'aria-label="{esc(t["refresh"])}" role="button">{icon("refresh")}</a>')
    # The settings are Home Assistant's own page, outside the ingress frame
    actions += (f'<a href="/config/app/{esc(slug)}/config" target="_top" class="icon-button" '
                f'title="{esc(t["settings"])}" aria-label="{esc(t["settings"])}">{icon("settings")}</a>'
                if slug else "")
    return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{esc(APP_NAME)}</title><style>{stylesheet()}</style><script>{SCRIPT}</script></head><body>"
            f'<header><h1>{esc(APP_NAME)}</h1><div class="actions">{actions}</div></header>'
            f'<main>{body}</main><footer class="version">{esc(f"{APP_NAME} {version()}".strip())}</footer>'
            "</body></html>")


# --- The server ------------------------------------------------------------------------------
class Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self) -> None:
        # HTTPServer looks up its own name here, which can wait for DNS
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "nvme-web", self.server_address[1]


# The page is rendered on the server and loads nothing: its own inline style
# and script, the fetch of its fresh copy, and Home Assistant's frame around it
HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                               "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
                               "frame-ancestors 'self'",
}


def handler(state: State, slug: str | None, allowed: set[str]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "nvme-web"

        def do_GET(self) -> None:  # noqa: N802 (the standard library's name)
            if allowed and self.client_address[0] not in allowed:
                self._send(403, "text/plain", b"only through Home Assistant")
            elif self.path.split("?", 1)[0] != "/":
                self._send(404, "text/plain", b"not found")
            else:
                page = render(state, lang_of(self.headers.get("Cookie"), self.headers.get("Accept-Language")), slug)
                self._send(200, "text/html; charset=utf-8", page.encode())

        def _send(self, code: int, kind: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            for name, value in HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:  # no access log
            pass

    return Handler


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s",
                        datefmt="%H:%M:%S", stream=sys.stdout)
    # The app's slug, e.g. a4b1f1df_nvme_monitor; the Supervisor names the container's host after it
    slug = os.environ.get("HOSTNAME", "").replace("-", "_") or None
    state = State(History(HISTORY))
    threading.Thread(target=state.run, daemon=True).start()
    # s6 stops the service with TERM; leave through finally to save the history
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    server = Server(("0.0.0.0", PORT), handler(state, slug, {INGRESS_PEER}))
    LOGGER.info("Web page on port %d (ingress)", PORT)
    try:
        server.serve_forever()
    finally:
        with state.lock:
            state.history.save(time.time())


if __name__ == "__main__":
    main()
