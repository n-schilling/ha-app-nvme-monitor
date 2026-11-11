"""The Page check, and the screenshots in docs/screenshots.

    docker build --build-arg BUILD_VERSION=<version> -t nvme-monitor:page nvme_monitor
    uv run --with playwright==1.65.0 python scripts/screenshots.py --image nvme-monitor:page

Starts the web page of the built image in a throwaway container with made-up
demo data (status files and two months of history written here; nothing is
read from a real drive) and opens it with a headless browser the way Home
Assistant's ingress does: through a small proxy at the Supervisor's address
172.30.32.2 under an ingress path, once on its own and once in a frame. It
opens every view (Overview, Health, Temperature, History) by its URL and
checks the tabs (after the name in its row, on a phone a bar with icons at
the bottom, the right one active; tab, reload and back in the frame) and the
header every app page shares (the icon, the app's name, the stamp, the
refresh button, the settings link with target _top and the app's slug), the
version line, that there are no console errors and no CSP violations, and
light, dark, German and phone width, and that the page takes Home
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
# hass names that language (and light mode), as share/ha-app/ha-lang.js reads it
PROXY = r"""
import http.server, re, sys, urllib.error, urllib.parse, urllib.request
APP, PREFIX = sys.argv[1], sys.argv[2]
OUTER = ('<!doctype html><html><head><meta charset="utf-8"><title>Home Assistant</title></head>'
         '<body style="margin:0">HASS<iframe src="%s" style="border:0;width:100vw;height:100vh"></iframe></body></html>'
         % PREFIX)
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


def version() -> str:
    for line in (ROOT / "nvme_monitor/config.yaml").read_text().splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip('"')
    raise SystemExit("no version in config.yaml")


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
    """Console errors, script errors and CSP violations, in every frame."""
    page.add_init_script("""document.addEventListener("securitypolicyviolation",
        (e) => console.error("CSP violation: " + e.violatedDirective + " " + e.blockedURI));""")
    page.on("console", lambda m: problems.check(m.type != "error", f"{where}: console error: {m.text} ({m.location.get('url')})"))
    page.on("pageerror", lambda e: problems.append(f"{where}: script error: {e}"))


# The views (tabs in the header): where each one is, and the labels
TABS = (("overview", "?"), ("health", "?tab=health"), ("temperature", "?tab=temperature"), ("history", "?tab=history"))
TAB_LABELS = {"en": ["Overview", "Health", "Temperature", "History"],
              "de": ["Übersicht", "Gesundheit", "Temperatur", "Verlauf"]}


def check_header(page: Page | Frame, problems: Problems, where: str, lang: str, phone: bool,
                 tab: str = "overview") -> None:
    header = page.evaluate("""() => {
        const h = document.querySelector("body > header");
        const actions = h && h.querySelector(":scope > .actions");
        return h && {
            tags: [...h.children].slice(0, 3).map((k) => k.tagName.toLowerCase() + (k.className ? "." + k.className.split(" ").join(".") : "")),
            h1: h.querySelector("h1")?.textContent,
            // The store icon left of the name: loaded, 24 px, in the row of the name
            icon: h.firstElementChild.matches("img.app-icon") ? (() => {
              const box = h.firstElementChild.getBoundingClientRect(), name = h.querySelector(":scope > h1").getBoundingClientRect();
              return {src: h.firstElementChild.getAttribute("src"), loaded: h.firstElementChild.complete && h.firstElementChild.naturalWidth > 0,
                      size: [box.width, box.height], row: Math.abs((box.top + box.bottom) / 2 - (name.top + name.bottom) / 2) < 2};
            })() : null,
            // The views: tabs right after the name, in its row (on a phone a bar at the bottom of the window)
            tabs: (() => {
              const nav = h.querySelector(":scope > nav.tabs");
              if (!nav) return null;
              const name = h.querySelector(":scope > h1").getBoundingClientRect(), box = nav.getBoundingClientRect();
              const mid = (r) => (r.top + r.bottom) / 2;
              return {labels: [...nav.children].map((a) => a.textContent), hrefs: [...nav.children].map((a) => a.getAttribute("href")),
                      active: [...nav.querySelectorAll("a.active[aria-current=page]")].map((a) => a.getAttribute("href")),
                      row: Math.abs(mid(box) - mid(name)) < 2, bottom: Math.abs(box.bottom - window.innerHeight) < 1,
                      icons: [...nav.children].every((a) => a.querySelector("svg") && getComputedStyle(a.querySelector("svg")).display !== "none")};
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
    problems.check(header["tags"] == ["img.app-icon", "h1", "nav.tabs"] and header["h1"] == w.APP_NAME,
                   f"{where}: the header does not start with the icon, <h1>{w.APP_NAME}</h1>, the tabs: {header['tags']}")
    tabs = header["tabs"] or {}
    problems.check(tabs.get("labels") == TAB_LABELS[lang] and tabs.get("hrefs") == [href for _, href in TABS],
                   f"{where}: the tabs are {tabs.get('labels')} {tabs.get('hrefs')}")
    problems.check(tabs.get("active") == [dict(TABS)[tab]], f"{where}: the active tab is {tabs.get('active')}, not {tab}")
    if phone:
        problems.check(bool(tabs.get("bottom") and tabs.get("icons")), f"{where}: the tabs are no bar with icons at the bottom: {tabs}")
    else:
        problems.check(bool(tabs.get("row") and not tabs.get("icons")), f"{where}: the tabs are not in the row of the name: {tabs}")
    problems.check(header["icon"] == {"src": "icon.png", "loaded": True, "size": [24, 24], "row": True},
                   f"{where}: the app's icon does not show, 24 px, in the row of the name: {header['icon']}")
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
    expected = f'<footer class="version">{w.APP_NAME} {version()}</footer>'
    problems.check(header["footer"] == expected, f"{where}: the last element is {header['footer']}, not {expected}")
    problems.check(not header["wide"], f"{where}: the page is wider than the window")


def check_tabs_in_frame(page: Page, base: str, problems: Problems) -> None:
    """Tabs are links, the view is in the URL: tab, reload and back in Home Assistant's frame."""
    frame = page.frame_locator("iframe")
    app = next(f for f in page.frames if f.parent_frame is not None)
    try:
        for label, href in (("History", "?tab=history"), ("Health", "?tab=health")):
            with page.expect_event("framenavigated", predicate=lambda f: f is app):
                frame.locator("nav.tabs > a", has_text=label).click()
            frame.locator("nav.tabs > a.active", has_text=label).wait_for()
            problems.check(app.url == base + INGRESS + href, f"frame: the {label} tab opens {app.url}, not {href}")
        with page.expect_event("framenavigated", predicate=lambda f: f is app):
            frame.locator("#refresh").click()
        frame.locator("nav.tabs > a.active", has_text="Health").wait_for()
        problems.check(app.url == base + INGRESS + "?tab=health", f"frame: refresh leaves the view ({app.url})")
        with page.expect_event("framenavigated", predicate=lambda f: f is app):
            app.evaluate("setTimeout(() => history.back(), 0)")  # the browser's back, inside the frame
        frame.locator("nav.tabs > a.active", has_text="History").wait_for()
        problems.check(app.url == base + INGRESS + "?tab=history", f"frame: back does not return to the view ({app.url})")
        with page.expect_event("framenavigated", predicate=lambda f: f is app):
            frame.locator("nav.tabs > a", has_text="Overview").click()
        with page.expect_event("framenavigated", predicate=lambda f: f is app):
            frame.locator("a.state[href='?tab=health']").first.click()
        frame.locator("nav.tabs > a.active", has_text="Health").wait_for()
    except PlaywrightError as err:
        problems.append(f"frame: the tabs do not work: {err.message.splitlines()[0]}")


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
                        check_header(page, problems, f"{where} {tab}", lang, width < 600, tab)
                        if tab in shots or (tab == "overview" and "phone-dark" in shots):
                            path = args.out / f"{'phone-dark' if 'phone-dark' in shots else tab}.png"
                            page.screenshot(path=path, full_page=tab != "overview")
                            print(path)
                    # The refresh button reloads the page and stays on the view
                    try:
                        with page.expect_navigation(wait_until="load", timeout=10_000):
                            page.click("#refresh", timeout=5_000)
                        check_header(page, problems, f"{where} after refresh", lang, width < 600, "history")
                    except PlaywrightError as err:
                        problems.append(f"{where}: the refresh button does not reload the page: {err.message.splitlines()[0]}")
                    # In Home Assistant's frame: allowed by the page's CSP, same header
                    page.goto(base + "/", wait_until="load")
                    frame = page.frame_locator("iframe")
                    frame.locator("footer.version").wait_for()
                    problems.check(frame.locator("h1").text_content() == w.APP_NAME, f"{where}: no page in the frame")
                    if (lang, scheme) == ("en", "light"):
                        check_tabs_in_frame(page, base, problems)
                    context.close()
                check_language(browser, base, problems)
                browser.close()
        finally:
            stop(name)
    if problems:
        print("Page check FAILED:", *problems, sep="\n  ", file=sys.stderr)
        raise SystemExit(1)
    print("Page check passed: header, version line, no console errors or CSP violations, light, dark, de, "
          "phone width, Home Assistant's language")


if __name__ == "__main__":
    main()
