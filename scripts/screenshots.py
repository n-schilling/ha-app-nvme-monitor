"""The Page check, and the screenshots in docs/screenshots.

    docker build --build-arg BUILD_VERSION=<version> -t nvme-monitor:page nvme_monitor
    uv run --with playwright==1.65.0 python scripts/screenshots.py --image nvme-monitor:page

Starts the web page of the built image in a throwaway container with made-up
demo data (status files and two months of history written here; nothing is
read from a real drive) and opens it with a headless browser the way Home
Assistant's ingress does: through a small proxy at the Supervisor's address
172.30.32.2 under an ingress path, once on its own and once in a frame. It
opens every view (Overview, Health, Temperature, History) by its URL and
checks the tabs (centred in the header's row, icon beside label and a line
under the active one, no bottom bar; narrow, below 870 px, a bar at the
bottom, icon over label; the right one active; tab, reload and back in the
frame) and the header every app page shares, as Home Assistant's own pages
draw it (hass-tabs-subpage, STANDARD.md §7: the menu button first, shown only
when narrow; the h1 with the name exactly as `name` in config.yaml, hidden
beside the tabs like the icon; menu and buttons within the header, the stamp
(hidden when narrow), the refresh button that reloads and moves the stamp,
the settings link with target _top and the app's slug), Home Assistant's part
of the frame (the stand-in plays it, share/ha-app/ha-frame.js the page's: no
kiosk mode on a wide screen; on a phone, 390 x 844 with a notch of 47 px and a
home bar of 34 px and without, kiosk mode, so no bar of Home Assistant above
the app, the header from the top and 56 px plus the notch, the bar at the
bottom clear of the home bar, every tab on the screen, no sideways scrolling;
the menu button opens Home Assistant's sidebar; wider and back switches kiosk
mode off and on with one message each way), the version line, the security headers of every answer of the page (STANDARD.md
§3), that there are no console errors and no CSP violations, and light, dark, German and phone width, and that the page takes Home
Assistant's language (a stand-in for its frontend in German, English and
French around the frame) over the browser's, reloading once. Then it takes
the screenshots, into docs/screenshots or the folder given with --out.

The browser is the installed Google Chrome, else Playwright's Chromium
(`uv run --with playwright==1.65.0 playwright install chromium` once).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["TZ"] = "UTC"
time.tzset()
sys.path.insert(0, str(ROOT / "nvme_monitor/rootfs/usr/local/lib"))

import nvme_web as w  # noqa: E402
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Frame, Page, sync_playwright  # noqa: E402

DAY, HOUR = 86400, 3600
UNITS_PER_GB = 1e9 / w.UNIT
# The Supervisor's network; the page only answers the ingress proxy at .2
SUBNET, PROXY_IP, APP_IP = "172.30.32.0/23", w.INGRESS_PEER, "172.30.32.10"
HOST = "a1b2c3d4-nvme-monitor"   # the Supervisor names the container's host after the slug
SLUG = HOST.replace("-", "_")
INGRESS = "/api/hassio_ingress/demo-token/"

# Home Assistant's ingress, as far as the page sees it: the path prefix, the
# client address and the frame of Home Assistant's page around it; with
# /?lang=xx also a stand-in for the frontend's <home-assistant> element, whose
# hass names that language (and light mode), as share/ha-app/ha-lang.js reads it;
# with /?safe=47,34 a phone with a notch and a home bar: the safe-area insets top
# and bottom in px that Home Assistant passes on.
#
# The outer page plays Home Assistant's part (its app panel, ha-panel-app) for
# the page's share/ha-app/ha-frame.js: it answers subscribe-properties with
# properties (narrow below 870 px, the insets, the route) and again on every
# resize; it shows its 40 px bar #ha-bar above the frame while narrow and not in
# kiosk mode, and pads the frame by the insets unless the page handles them
# (handleSafeArea). What the page asked for lands in window.__kiosk,
# window.__safe and window.__menu (toggle-menu clicks), every message in
# window.__messages.
PROXY = r"""
import http.server, re, sys, urllib.error, urllib.parse, urllib.request
APP, PREFIX = sys.argv[1], sys.argv[2]
OUTER = ('<!doctype html><html><head><meta charset="utf-8"><title>Home Assistant</title>'
         '<style>html, body { margin: 0; height: 100%; } body { display: flex; flex-direction: column; }'
         ' #ha-bar { flex: none; height: 40px; background: #888; }'
         ' #ha-app { flex: 1; min-height: 0; box-sizing: border-box; }'
         ' iframe { margin: 0; border: 0; width: 100%; height: 100%; display: block; }</style>'
         '<script>FRAME</script></head><body>HASS<div id="ha-bar" hidden></div><div id="ha-app">'
         '<iframe id="ingress" title="app" src="INGRESS"></iframe></div></body></html>')
# Home Assistant's app panel as far as ha-frame.js talks to it; in the head, so
# it listens before the frame loads
FRAME = '''(() => {
  const safe = (new URLSearchParams(location.search).get("safe") || "0,0").split(",");
  const inset = (n) => (parseInt(n, 10) || 0) + "px";
  const insets = { top: inset(safe[0]), right: "0px", bottom: inset(safe[1]), left: "0px" };
  const frame = () => document.getElementById("ingress").contentWindow;
  Object.assign(window, { __kiosk: null, __safe: null, __menu: 0, __messages: [] });
  let subscribed = false;
  const narrow = () => matchMedia("(max-width: 870px)").matches;
  const layout = () => {
    const bar = document.getElementById("ha-bar"), box = document.getElementById("ha-app");
    if (!bar || !box) return;
    bar.hidden = !(narrow() && !window.__kiosk);
    box.style.paddingTop = window.__safe || !bar.hidden ? "0px" : insets.top;
    box.style.paddingBottom = window.__safe ? "0px" : insets.bottom;
  };
  const send = () => {
    if (subscribed) frame().postMessage({ type: "home-assistant/properties", narrow: narrow(),
      route: { prefix: "/app/demo", path: "" }, safeAreaInsets: insets }, location.origin);
  };
  window.addEventListener("message", (e) => {
    if (e.source !== frame() || !e.data) return;
    window.__messages.push(e.data);
    if (e.data.type === "home-assistant/subscribe-properties") {
      subscribed = true;
      window.__kiosk = !!e.data.kioskMode;
      window.__safe = !!e.data.handleSafeArea;
      layout();
      send();
    } else if (e.data.type === "home-assistant/unsubscribe-properties") {
      subscribed = false;
      window.__kiosk = false;
      window.__safe = false;
      layout();
    } else if (e.data.type === "home-assistant/toggle-menu") {
      window.__menu += 1;
    }
  });
  window.addEventListener("resize", () => { layout(); send(); });
  document.addEventListener("DOMContentLoaded", layout);
})();'''
OUTER = OUTER.replace("FRAME", FRAME).replace("INGRESS", PREFIX)
HASS = ('<home-assistant></home-assistant><script>document.querySelector("home-assistant").hass ='
        ' { language: "LANG", locale: { language: "LANG" }, themes: { darkMode: false } };</script>')
LANG = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?")
ICON = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><rect width="1" height="1" fill="#009ac7"/></svg>'
class Proxy(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/":
            lang = (urllib.parse.parse_qs(query).get("lang") or [""])[0]
            hass = HASS.replace("LANG", lang) if LANG.fullmatch(lang) else ""
            return self.answer(200, {"Content-Type": "text/html; charset=utf-8"}, OUTER.replace("HASS", hass).encode())
        if self.path == "/favicon.ico":  # Home Assistant has one; the browser asks for it
            return self.answer(200, {"Content-Type": "image/svg+xml"}, ICON)
        if not self.path.startswith(PREFIX):
            return self.answer(404, {"Content-Type": "text/plain"}, b"not found")
        headers = {"X-Ingress-Path": PREFIX.rstrip("/")}
        for name in ("Accept-Language", "Cookie"):
            if self.headers.get(name):
                headers[name] = self.headers[name]
        request = urllib.request.Request(APP + "/" + self.path[len(PREFIX):], headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as answer:
                self.answer(answer.status, answer.headers, answer.read())
        except urllib.error.HTTPError as err:
            self.answer(err.code, err.headers, err.read())
    def answer(self, code, headers, body):
        self.send_response(code)
        for name, value in headers.items():
            if name.lower() not in ("content-length", "connection", "transfer-encoding", "server", "date"):
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass
http.server.ThreadingHTTPServer(("0.0.0.0", 8080), Proxy).serve_forever()
"""


def config(key: str) -> str:
    for line in (ROOT / "nvme_monitor/config.yaml").read_text().splitlines():
        if line.startswith(f"{key}:"):
            return line.split(":", 1)[1].strip().strip('"')
    raise SystemExit(f"no {key} in config.yaml")


def version() -> str:
    return config("version")


# The name the app store shows; the header must show exactly this
NAME = config("name")


# --- Demo data ---------------------------------------------------------------------------
# Made-up GB written per day, newest last: busier on some days
WRITTEN = [4.2 + 3.1 * ((i * 7) % 5) / 4 + (2.5 if i % 7 == 3 else 0) for i in range(61)]


def smart(day: int, minute: int) -> dict:
    """The SMART log of a made-up 1 TB drive, `day` days ago, at `minute`
    minutes after midnight."""
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


def demo_data(status_dir: Path, web_dir: Path) -> None:
    """The status file of one drive, as the monitor writes it, and two months
    of history, every 10 minutes, with a week the host was off."""
    now = int(time.time())
    midnight = now // DAY * DAY
    minute_now = (now - midnight) // 60
    history = w.History(web_dir / "history.json")
    for day in range(60, -1, -1):
        if 34 <= day <= 40:
            continue
        for minute in range(0, 1440, 10):
            at = midnight - day * DAY + minute * 60
            if at <= now:
                history.add("nvme0", {"readable": True, "at": at, "smart": smart(day, minute)})
    history.save(now)
    status = {
        "drive": "nvme0", "name": "Host NVMe", "model": "Example NVMe SSD 1TB", "firmware": "2.1.0",
        "capacity": 1_000_204_886_016, "at": now - 20, "read_at": now - 20, "failing_since": None,
        "readable": True, "smart": smart(0, minute_now),
        "thresholds": {"warning_temp_c": 82, "critical_temp_c": 85, "throttle_stage1_c": 80, "throttle_stage2_c": 83},
        "heat_margin": 10, "throttling": None, "reasons": [], "errors": [],
    }
    (status_dir / "nvme0.json").write_text(json.dumps(status))


# --- The throwaway containers ------------------------------------------------------------
def docker(*args: str) -> str:
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout.strip()


def create_network(name: str) -> None:
    """The Supervisor's subnet; another check running at the same time may
    hold it for a while."""
    for _ in range(40):
        result = subprocess.run(["docker", "network", "create", "--subnet", SUBNET, name],
                                capture_output=True, text=True)
        if result.returncode == 0:
            return
        if "overlap" not in result.stderr:
            raise SystemExit(result.stderr.strip())
        time.sleep(3)
    raise SystemExit(f"the subnet {SUBNET} stays in use")


def start(image: str, tmp: Path, name: str) -> str:
    """Starts the page and the ingress proxy; the address of the proxy."""
    status_dir, web_dir = tmp / "status", tmp / "web"
    status_dir.mkdir()
    web_dir.mkdir()
    demo_data(status_dir, web_dir)
    # The page runs as its own user, as in the app; it writes its history
    for path in (tmp, status_dir, web_dir):
        path.chmod(0o777)
    for path in (*status_dir.iterdir(), *web_dir.iterdir()):
        path.chmod(0o666)
    create_network(name)
    docker("run", "-d", "--name", f"{name}-app", "--network", name, "--ip", APP_IP, "--hostname", HOST,
           "--user", "nvmeweb", "-e", "TZ=UTC", "-e", "NVME_INTERVAL=60",
           "-v", f"{status_dir}:/run/nvme-monitor:ro", "-v", f"{web_dir}:/data/web",
           "--entrypoint", "python3", image, "/usr/local/lib/nvme_web.py")
    docker("run", "-d", "--name", f"{name}-proxy", "--network", name, "--ip", PROXY_IP,
           "-p", "127.0.0.1::8080", "--user", "nobody", "--entrypoint", "python3", image,
           "-c", PROXY, f"http://{APP_IP}:{w.PORT}", INGRESS)
    port = docker("port", f"{name}-proxy", "8080/tcp").splitlines()[0].rsplit(":", 1)[1]
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            with urllib.request.urlopen(base + INGRESS, timeout=2) as answer:
                if b"Example NVMe SSD 1TB" in answer.read():
                    return base
        except OSError:
            pass
        time.sleep(0.5)
    print(docker("logs", f"{name}-app"), file=sys.stderr)
    raise SystemExit("the page did not answer through the ingress proxy")


def stop(name: str) -> None:
    for args in (["rm", "-f", f"{name}-app", f"{name}-proxy"], ["network", "rm", name]):
        subprocess.run(["docker", *args], capture_output=True)


# --- The checks --------------------------------------------------------------------------
class Problems(list):
    def check(self, ok: bool, what: str) -> None:
        if not ok:
            self.append(what)


def watch(page: Page, problems: Problems, where: str) -> None:
    """Console errors, script errors and CSP violations, in every frame, and
    the security headers of every answer of the page (not its icon)."""
    page.add_init_script("""document.addEventListener("securitypolicyviolation",
        (e) => console.error("CSP violation: " + e.violatedDirective + " " + e.blockedURI));""")
    page.on("console", lambda m: problems.check(m.type != "error", f"{where}: console error: {m.text} ({m.location.get('url')})"))
    page.on("pageerror", lambda e: problems.append(f"{where}: script error: {e}"))

    def answer(response) -> None:
        path = response.url.split("?", 1)[0]
        if INGRESS in path and not path.endswith("/icon.png"):
            security_headers(response.headers, problems, f"{where} {response.url.partition(INGRESS)[2] or '/'}")

    page.on("response", answer)


def security_headers(headers: dict[str, str], problems: Problems, where: str) -> None:
    """The headers STANDARD.md §3 asks of every answer of the page."""
    policy = {part.split()[0]: part.split()[1:]
              for part in headers.get("content-security-policy", "").split(";") if part.strip()}
    scripts = policy.get("script-src", [])
    problems.check(policy.get("default-src") == ["'none'"], f"{where}: CSP default-src 'none' ({policy.get('default-src')})")
    problems.check("'unsafe-inline'" not in scripts and any(s.startswith(("'sha256-", "'nonce-")) for s in scripts),
                   f"{where}: CSP script-src by hash or nonce ({scripts})")
    problems.check(policy.get("img-src") == ["'self'"], f"{where}: CSP img-src 'self' ({policy.get('img-src')})")
    problems.check(policy.get("frame-ancestors") == ["'self'"], f"{where}: CSP frame-ancestors 'self'")
    problems.check(policy.get("base-uri") == ["'none'"], f"{where}: CSP base-uri 'none'")
    problems.check(policy.get("form-action") == ["'none'"], f"{where}: CSP form-action 'none'")
    problems.check(headers.get("x-content-type-options") == "nosniff", f"{where}: X-Content-Type-Options nosniff")
    problems.check(headers.get("referrer-policy") in ("no-referrer", "same-origin"),
                   f"{where}: Referrer-Policy ({headers.get('referrer-policy')})")
    problems.check(headers.get("cache-control") == "no-store", f"{where}: Cache-Control no-store")


# The views (tabs in the header): where each one is, and the labels
TABS = (("overview", "?tab=overview"), ("health", "?tab=health"), ("temperature", "?tab=temperature"), ("history", "?tab=history"))
TAB_LABELS = {"en": ["Overview", "Health", "Temperature", "History"],
              "de": ["Übersicht", "Gesundheit", "Temperatur", "Verlauf"]}


def check_header(page: Page | Frame, problems: Problems, where: str, lang: str, phone: bool,
                 tab: str = "overview", safe: tuple[int, int] = (0, 0)) -> None:
    """The header as Home Assistant's own pages draw it (hass-tabs-subpage, STANDARD.md §7):
    phone is Home Assistant's narrow, safe the insets top and bottom the page keeps clear of."""
    header = page.evaluate("""() => {
        const h = document.querySelector("body > header");
        if (!h) return null;
        const actions = h.querySelector(":scope > .actions");
        const box = h.getBoundingClientRect(), shown = (e) => !!e && getComputedStyle(e).display !== "none";
        const m = h.querySelector(":scope > .menu"), h1 = h.querySelector(":scope > h1");
        const nav = h.querySelector(":scope > nav.tabs");
        const within = (e) => { const r = e.getBoundingClientRect();
          return r.width > 0 && r.left >= box.left - 0.5 && r.right <= box.right + 0.5 && r.top >= box.top - 0.5 && r.bottom <= box.bottom + 0.5; };
        return {
            tags: [...h.children].map((k) => k.tagName.toLowerCase() + (k.className ? "." + k.className.split(" ").join(".") : "")),
            top: box.top, height: box.height, narrow: document.documentElement.classList.contains("narrow"),
            h1: h1?.textContent, h1Hidden: !!h1 && h1.getBoundingClientRect().width <= 1 && h1.getBoundingClientRect().height <= 1,
            brand: [...h.querySelectorAll("img")].some(shown),
            menu: m && {first: h.firstElementChild === m, ha: m.hasAttribute("data-ha-menu"), role: m.getAttribute("role"),
                        title: m.title, label: m.getAttribute("aria-label"), path: m.querySelector("svg path")?.getAttribute("d"),
                        shown: shown(m)},
            // The menu and the visible buttons within the header's box (below the notch)
            inside: [m, ...(actions ? [...actions.children] : [])].filter((e) => e && e.offsetParent && shown(e))
              .filter((e) => !within(e)).map((e) => e.tagName.toLowerCase() + (e.id ? "#" + e.id : "")),
            tabs: nav && (() => {
              const tabs = [...nav.querySelectorAll(":scope > a.tab")], first = tabs[0].getBoundingClientRect();
              const last = tabs[tabs.length - 1].getBoundingClientRect(), r = nav.getBoundingClientRect();
              const a = actions.getBoundingClientRect(), pad = getComputedStyle(nav).paddingBottom;
              const after = (t) => getComputedStyle(t, "::after").content;
              return {labels: tabs.map((t) => t.textContent), hrefs: tabs.map((t) => t.getAttribute("href")),
                      others: nav.querySelectorAll(":scope > :not(a.tab)").length,
                      active: [...nav.querySelectorAll("a.tab.active[aria-current=page]")].map((t) => t.getAttribute("href")),
                      icons: tabs.every((t) => shown(t.querySelector("svg"))),
                      fixed: getComputedStyle(nav).position === "fixed",
                      // Wide: in the header's row, centred between its start and the actions
                      row: tabs.every(within), centred: Math.abs((first.left + last.right) / 2 - (r.left + r.right) / 2) < 1
                           && r.right <= a.left + 0.5,
                      // Narrow: the bar at the bottom of the window, clear of the home bar, every tab on the screen
                      bottom: r.bottom - innerHeight, pad,
                      onScreen: tabs.every((t) => { const q = t.getBoundingClientRect();
                        return q.left >= 0 && q.right <= innerWidth && q.bottom <= innerHeight - parseFloat(pad) + 0.5; }),
                      line: tabs.map((t) => after(t) !== "none" && after(t) !== "")};
            })(),
            actions: actions ? [...actions.children].map((e) => ({
                tag: e.tagName.toLowerCase(), id: e.id, cls: e.className, text: e.textContent,
                href: e.getAttribute("href"), target: e.getAttribute("target"), title: e.title,
                role: e.getAttribute("role"), path: e.querySelector("svg path")?.getAttribute("d"),
                visible: !!e.offsetParent })) : [],
            footer: document.body.lastElementChild.outerHTML,
            wide: document.documentElement.scrollWidth > document.documentElement.clientWidth };
    }""")
    if not header:
        problems.append(f"{where}: no header")
        return
    de = lang == "de"
    menu = "Menü" if de else "Menu"
    problems.check(header["narrow"] == phone, f"{where}: html.narrow is {header['narrow']}, not {phone}")
    problems.check(header["tags"] == ["a.icon-button.menu", "h1.name", "nav.tabs", "div.actions"] and header["h1"] == NAME,
                   f"{where}: the header does not hold the menu button, <h1>{NAME}</h1> (config.yaml), the tabs and "
                   f"the actions: {header['tags']} {header['h1']!r}")
    problems.check(header["h1Hidden"] and not header["brand"], f"{where}: an icon or a visible name beside the tabs")
    problems.check(abs(header["top"]) < 0.5 and abs(header["height"] - 56 - safe[0]) < 0.5,
                   f"{where}: the header is not from the top and 56 + {safe[0]} px high ({header['top']}, {header['height']})")
    m = header["menu"] or {}
    problems.check(bool(m.get("first") and m.get("ha") and m.get("role") == "button" and m.get("title") == menu == m.get("label")
                        and m.get("path") == w.ICONS["menu"] and m.get("shown") == phone),
                   f"{where}: the menu button is not first and {'shown' if phone else 'hidden'}: {m}")
    tabs = header["tabs"] or {}
    problems.check(tabs.get("labels") == TAB_LABELS[lang] and tabs.get("hrefs") == [href for _, href in TABS]
                   and tabs.get("others") == 0,
                   f"{where}: the tabs are {tabs.get('labels')} {tabs.get('hrefs')}")
    problems.check(tabs.get("active") == [dict(TABS)[tab]], f"{where}: the active tab is {tabs.get('active')}, not {tab}")
    problems.check(bool(tabs.get("icons")), f"{where}: a tab without its icon")
    if phone:
        problems.check(bool(tabs.get("fixed") and abs(tabs.get("bottom", 1)) < 0.5 and tabs.get("pad") == f"{safe[1]}px"
                            and tabs.get("onScreen") and not any(tabs.get("line", []))),
                       f"{where}: the tabs are no bar at the bottom, {safe[1]} px clear of the home bar, all on the screen: {tabs}")
    else:
        problems.check(bool(not tabs.get("fixed") and tabs.get("row") and tabs.get("centred")
                            and tabs.get("line") == [href == dict(TABS)[tab] for _, href in TABS]),
                       f"{where}: the tabs are not centred in the header's row with a line under the active one: {tabs}")
    problems.check(not header["inside"], f"{where}: outside the header's box: {header['inside']}")
    a = header["actions"]
    problems.check([(x["tag"], x["id"]) for x in a] == [("span", ""), ("a", "refresh"), ("a", "")],
                   f"{where}: the actions are not stamp, refresh, settings: {[(x['tag'], x['id']) for x in a]}")
    if len(a) != 3:
        return
    stamp, refresh, settings = a
    problems.check("stamp" in stamp["cls"].split() and "opt" in stamp["cls"].split()
                   and re.fullmatch(r"(Stand|As of) \d\d:\d\d:\d\d", stamp["text"])
                   and stamp["text"].startswith("Stand" if de else "As of"),
                   f"{where}: the stamp is {stamp['cls']!r} {stamp['text']!r}")
    problems.check(stamp["visible"] != phone, f"{where}: the stamp is {'shown' if phone else 'hidden'}")
    problems.check(refresh["role"] == "button" and refresh["href"] == "" and "icon-button" in refresh["cls"]
                   and refresh["title"] == ("Aktualisieren" if de else "Refresh")
                   and refresh["path"] == w.ICONS["refresh"], f"{where}: the refresh button is {refresh}")
    problems.check(settings["href"] == f"/config/app/{SLUG}/config" and settings["target"] == "_top"
                   and "icon-button" in settings["cls"] and settings["title"] == ("Einstellungen" if de else "Settings")
                   and settings["path"] == w.ICONS["settings"], f"{where}: the settings link is {settings}")
    expected = f'<footer class="version">{NAME} {version()}</footer>'
    problems.check(header["footer"] == expected, f"{where}: the last element is {header['footer']}, not {expected}")
    problems.check(not header["wide"], f"{where}: the page is wider than the window")


def check_tabs_in_frame(page: Page, base: str, problems: Problems) -> None:
    """Tabs are links, the view is in the URL: tab, reload and back in Home Assistant's frame."""
    frame = page.frame_locator("iframe")
    app = next(f for f in page.frames if f.parent_frame is not None)
    try:
        for label, href in (("History", "?tab=history"), ("Health", "?tab=health")):
            with page.expect_event("framenavigated", predicate=lambda f: f is app):
                frame.locator("nav.tabs > a.tab", has_text=label).click()
            frame.locator("nav.tabs > a.tab.active", has_text=label).wait_for()
            problems.check(app.url == base + INGRESS + href, f"frame: the {label} tab opens {app.url}, not {href}")
        with page.expect_event("framenavigated", predicate=lambda f: f is app):
            frame.locator("#refresh").click()
        frame.locator("nav.tabs > a.tab.active", has_text="Health").wait_for()
        problems.check(app.url == base + INGRESS + "?tab=health", f"frame: refresh leaves the view ({app.url})")
        with page.expect_event("framenavigated", predicate=lambda f: f is app):
            app.evaluate("setTimeout(() => history.back(), 0)")  # the browser's back, inside the frame
        frame.locator("nav.tabs > a.tab.active", has_text="History").wait_for()
        problems.check(app.url == base + INGRESS + "?tab=history", f"frame: back does not return to the view ({app.url})")
        with page.expect_event("framenavigated", predicate=lambda f: f is app):
            frame.locator("nav.tabs > a.tab", has_text="Overview").click()
        frame.locator("nav.tabs > a.tab.active", has_text="Overview").wait_for()
        problems.check(app.url == base + INGRESS + "?tab=overview", f"frame: the Overview tab opens {app.url}")
        with page.expect_event("framenavigated", predicate=lambda f: f is app):
            frame.locator("a.state[href='?tab=health']").first.click()
        frame.locator("nav.tabs > a.tab.active", has_text="Health").wait_for()
    except PlaywrightError as err:
        problems.append(f"frame: the tabs do not work: {err.message.splitlines()[0]}")


def in_frame(page: Page, wide: bool, safe: tuple[int, int]) -> Frame:
    """The app's frame in the stand-in for Home Assistant, once the page has subscribed."""
    page.frame_locator("#ingress").locator("body > header h1").wait_for(state="attached")
    page.wait_for_function("window.__safe !== null")
    page.wait_for_timeout(500)  # room for the answer, and for any further message, which must not loop
    app = next(f for f in page.frames if f.parent_frame is not None)
    app.wait_for_selector("html:not(.narrow)" if wide else "html.narrow", state="attached")
    if safe != (0, 0):
        app.wait_for_function("getComputedStyle(document.documentElement).getPropertyValue('--safe-area-inset-top')"
                              f".trim() === '{safe[0]}px'")
    return app


def check_frame(page: Page, app: Frame, problems: Problems, where: str, lang: str, wide: bool,
                safe: tuple[int, int]) -> None:
    """What the page asked of Home Assistant (the stand-in records it) and where its frame sits."""
    seen = page.evaluate("""() => ({kiosk: window.__kiosk, safe: window.__safe, menu: window.__menu,
      messages: window.__messages.map((m) => m.type + (m.type.endsWith("/subscribe-properties") ? ":" + m.kioskMode : "")),
      bar: getComputedStyle(document.getElementById("ha-bar")).display !== "none",
      frame: (() => { const r = document.getElementById("ingress").getBoundingClientRect(); return [r.top, r.bottom - innerHeight]; })()})""")
    problems.check(seen["kiosk"] is (not wide), f"{where}: kiosk mode {'' if wide else 'not '}requested ({seen['messages']})")
    problems.check(seen["safe"] is True, f"{where}: the page does not keep clear of the notch itself (handleSafeArea)")
    problems.check(not seen["bar"], f"{where}: a bar of Home Assistant above the app")
    problems.check(seen["frame"] == [0, 0], f"{where}: the frame does not fill the window ({seen['frame']})")
    problems.check(len(seen["messages"]) <= 2, f"{where}: a loop of messages ({seen['messages']})")
    check_header(app, problems, where, lang, not wide, "overview", safe)
    problems.check(app.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"{where}: the page scrolls sideways")


def check_frames(browser, base: str, problems: Problems, out: Path | None) -> None:
    """Home Assistant's part of the frame (the stand-in plays it): kiosk mode only
    while narrow, the notch and the home bar by Home Assistant's insets, the menu."""
    for width, height, safe, lang in ((1200, 760, (0, 0), "en"), (390, 844, (47, 34), "de"), (390, 844, (0, 0), "en")):
        wide = width > 870
        where = f"frame {width}px" + (f", notch {safe[0]} px, home bar {safe[1]} px" if safe != (0, 0) else "")
        context = browser.new_context(viewport={"width": width, "height": height}, color_scheme="dark",
                                      locale="de-DE" if lang == "de" else "en-US", device_scale_factor=1 if wide else 2)
        page = context.new_page()
        watch(page, problems, where)
        try:
            page.goto(base + "/" + ("?safe=" + ",".join(map(str, safe)) if safe != (0, 0) else ""), wait_until="load")
            app = in_frame(page, wide, safe)
            check_frame(page, app, problems, where, lang, wide, safe)
            if out and not wide and safe != (0, 0):
                page.screenshot(path=out / "phone-notch.png")
                print(out / "phone-notch.png")
            if not wide:
                app.locator("header .menu").click()
                page.wait_for_timeout(200)
                problems.check(page.evaluate("window.__menu") == 1, f"{where}: the menu button does not open Home Assistant's sidebar")
            if safe != (0, 0):
                # Wider than narrow and back: kiosk mode off (Home Assistant's bar back), then on again
                page.set_viewport_size({"width": 1200, "height": height})
                app.wait_for_selector("html:not(.narrow)", state="attached")
                page.wait_for_timeout(300)
                problems.check(page.evaluate("window.__kiosk") is False, f"{where}: wide again, kiosk mode stays on")
                page.set_viewport_size({"width": width, "height": height})
                app.wait_for_selector("html.narrow", state="attached")
                page.wait_for_timeout(300)
                sent = page.evaluate("""window.__messages.filter((m) => m.type.endsWith("-properties"))
                    .map((m) => m.type.split("/")[1] + (m.type.endsWith("/subscribe-properties") ? ":" + m.kioskMode : ""))""")
                problems.check(page.evaluate("window.__kiosk") is True and sent == [
                    "subscribe-properties:true", "unsubscribe-properties", "subscribe-properties:false",
                    "subscribe-properties:true"], f"{where}: narrow again, not kiosk mode once more and no more messages ({sent})")
                check_header(app, problems, f"{where}, narrow again", lang, True, "overview", safe)
        except PlaywrightError as err:
            problems.append(f"{where}: {err.message.splitlines()[0]}")
        context.close()


def check_language(browser, base: str, problems: Problems) -> None:
    """The browser asks for English; the page follows Home Assistant's language
    (share/ha-app/ha-lang.js): one reload when it changes, none when not."""
    context = browser.new_context(viewport={"width": 1100, "height": 1000}, locale="en-US",
                                  extra_http_headers={"Accept-Language": "en"})
    page = context.new_page()
    watch(page, problems, "language")
    for ha_lang, lang, expected in (("de", "de", 2), ("de", "de", 1), ("en-GB", "en", 2), ("fr", "en", 2),
                                    ("fr", "en", 1)):
        where = f"Home Assistant in {ha_lang}"
        app, loads = in_home_assistant(page, base, ha_lang)
        shown = app.evaluate("[document.documentElement.lang, getComputedStyle(document.documentElement).visibility]")
        problems.check(shown == [lang, "visible"], f"{where}: page not in {lang} or not shown ({shown})")
        problems.check(loads == expected, f"{where}: loaded {loads} time(s), not {expected}")
        check_header(app, problems, where, lang, False)
        held = [(k["value"], k["path"]) for k in context.cookies() if k["name"] == "ha_lang"]
        problems.check(held == [(ha_lang.split("-")[0], INGRESS)], f"{where}: the cookie is {held}")
    context.close()


def in_home_assistant(page: Page, base: str, lang: str) -> tuple[Frame, int]:
    """Opens the page in a stand-in for Home Assistant's frontend in that
    language; the app's frame and how often it loaded."""
    loads: list[str] = []

    def seen(frame: Frame) -> None:
        if frame.parent_frame is not None and frame.url.startswith(base + INGRESS):
            loads.append(frame.url)

    page.on("framenavigated", seen)
    page.goto(f"{base}/?lang={lang}", wait_until="load")
    page.wait_for_timeout(1500)  # room for another reload, which must not come
    page.remove_listener("framenavigated", seen)
    app = next(f for f in page.frames if f.parent_frame is not None)
    app.locator("body > header h1").wait_for()
    return app, len(loads)


def dark(page: Page) -> bool:
    rgb = page.evaluate("getComputedStyle(document.body).backgroundColor")
    r, g, b = (int(x) for x in re.findall(r"\d+", rgb)[:3])
    return r + g + b < 3 * 128


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", default="nvme-monitor:page", help="the built image (default: %(default)s)")
    parser.add_argument("--out", type=Path, default=ROOT / "docs/screenshots", help="folder for the screenshots")
    parser.add_argument("--notch", type=Path, help="also a screenshot of a phone with a notch in Home Assistant's frame,"
                        " into this folder (not one of the docs' screenshots)")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    name = f"nvme-page-{os.getpid()}"
    problems = Problems()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            base = start(args.image, Path(tmp), name)
            with sync_playwright() as p:
                chrome = Path("/Applications/Google Chrome.app").exists() or shutil.which("google-chrome")
                browser = p.chromium.launch(channel="chrome" if chrome else None)
                for lang, scheme, width, height, shots in (
                    ("en", "light", 1100, 1000, ("overview", "health", "temperature", "history")),
                    ("en", "dark", 390, 844, ("phone-dark",)),
                    ("de", "light", 1100, 1000, ()),
                    ("de", "dark", 1100, 1000, ()),
                ):
                    where = f"{lang} {scheme} {width}px"
                    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme,
                                                  locale="de-DE" if lang == "de" else "en-US",
                                                  device_scale_factor=2 if width < 600 else 1)
                    page = context.new_page()
                    watch(page, problems, where)
                    page.goto(base + INGRESS, wait_until="load")
                    problems.check(dark(page) == (scheme == "dark"), f"{where}: the page is not {scheme}")
                    # Every view by its URL; the history last, for the refresh below
                    for tab, href in TABS:
                        if tab != "overview":
                            page.goto(base + INGRESS + href, wait_until="load")
                        check_header(page, problems, f"{where} {tab}", lang, width <= 870, tab)
                        if tab in shots or (tab == "overview" and "phone-dark" in shots):
                            path = args.out / f"{'phone-dark' if 'phone-dark' in shots else tab}.png"
                            page.screenshot(path=path, full_page=tab != "overview")
                            print(path)
                    # The refresh button reloads the page, stays on the view and moves the stamp
                    # (the stamp is the server's time of the request, to the second)
                    stamp = page.locator("header .stamp").text_content()
                    page.wait_for_timeout(1100)
                    try:
                        with page.expect_navigation(wait_until="load", timeout=10_000):
                            page.click("#refresh", timeout=5_000)
                        check_header(page, problems, f"{where} after refresh", lang, width <= 870, "history")
                        moved = page.locator("header .stamp").text_content()
                        problems.check(moved != stamp, f"{where}: refresh leaves the stamp at {moved!r}")
                    except PlaywrightError as err:
                        problems.append(f"{where}: the refresh button does not reload the page: {err.message.splitlines()[0]}")
                    # In Home Assistant's frame: allowed by the page's CSP, same header
                    page.goto(base + "/", wait_until="load")
                    frame = page.frame_locator("iframe")
                    frame.locator("footer.version").wait_for()
                    problems.check(frame.locator("h1").text_content() == NAME, f"{where}: no page in the frame")
                    if (lang, scheme) == ("en", "light"):
                        check_tabs_in_frame(page, base, problems)
                    context.close()
                check_frames(browser, base, problems, args.notch)
                check_language(browser, base, problems)
                browser.close()
        finally:
            stop(name)
    if problems:
        print("Page check FAILED:", *problems, sep="\n  ", file=sys.stderr)
        raise SystemExit(1)
    print("Page check passed: header, version line, security headers, no console errors or CSP violations, "
          "light, dark, de, phone width, Home Assistant's frame (wide, phone with and without a notch), Home Assistant's language")


if __name__ == "__main__":
    main()
