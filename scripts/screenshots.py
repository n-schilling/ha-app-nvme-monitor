"""The Page check, and the screenshots in docs/screenshots.

    docker build --build-arg BUILD_VERSION=<version> -t nvme-monitor:page nvme_monitor
    uv run --with playwright==1.65.0 python scripts/screenshots.py --image nvme-monitor:page

Starts the web page of the built image in a throwaway container with made-up
demo data (status files and two months of history written here; nothing is
read from a real drive) and opens it with a headless browser the way Home
Assistant's ingress does: through a small proxy at the Supervisor's address
172.30.32.2 under an ingress path, once on its own and once in a frame. It
checks the header every app page shares (the app's name, the stamp, the
refresh button, the settings link with target _top and the app's slug), the
version line, that there are no console errors and no CSP violations, and
light, dark, German and phone width. Then it takes the screenshots, into
docs/screenshots or the folder given with --out.

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
from playwright.sync_api import Page, sync_playwright  # noqa: E402

DAY, HOUR = 86400, 3600
UNITS_PER_GB = 1e9 / w.UNIT
# The Supervisor's network; the page only answers the ingress proxy at .2
SUBNET, PROXY_IP, APP_IP = "172.30.32.0/23", w.INGRESS_PEER, "172.30.32.10"
HOST = "a1b2c3d4-nvme-monitor"   # the Supervisor names the container's host after the slug
SLUG = HOST.replace("-", "_")
INGRESS = "/api/hassio_ingress/demo-token/"

# Home Assistant's ingress, as far as the page sees it: the path prefix, the
# client address and the frame of Home Assistant's page around it
PROXY = r"""
import http.server, sys, urllib.error, urllib.request
APP, PREFIX = sys.argv[1], sys.argv[2]
OUTER = ('<!doctype html><html><head><meta charset="utf-8"><title>Home Assistant</title></head>'
         '<body style="margin:0"><iframe src="%s" style="border:0;width:100vw;height:100vh"></iframe></body></html>'
         % PREFIX).encode()
ICON = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><rect width="1" height="1" fill="#009ac7"/></svg>'
class Proxy(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            return self.answer(200, {"Content-Type": "text/html; charset=utf-8"}, OUTER)
        if self.path == "/favicon.ico":  # Home Assistant has one; the browser asks for it
            return self.answer(200, {"Content-Type": "image/svg+xml"}, ICON)
        if not self.path.startswith(PREFIX):
            return self.answer(404, {"Content-Type": "text/plain"}, b"not found")
        headers = {"X-Ingress-Path": PREFIX.rstrip("/")}
        if self.headers.get("Accept-Language"):
            headers["Accept-Language"] = self.headers["Accept-Language"]
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


def check_header(page: Page, problems: Problems, where: str, lang: str, phone: bool) -> None:
    header = page.evaluate("""() => {
        const h = document.querySelector("body > header");
        const actions = h && h.querySelector(":scope > .actions");
        return h && {
            first: h.firstElementChild.outerHTML.slice(0, 40), h1: h.querySelector("h1")?.textContent,
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
    problems.check(header["first"].startswith("<h1>") and header["h1"] == w.APP_NAME,
                   f"{where}: the header does not start with <h1>{w.APP_NAME}</h1>: {header['first']}")
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
                    ("en", "light", 1100, 1000, ("overview", "history")),
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
                    check_header(page, problems, where, lang, width < 600)
                    problems.check(dark(page) == (scheme == "dark"), f"{where}: the page is not {scheme}")
                    for shot in shots:
                        path = args.out / f"{shot}.png"
                        if shot == "history":
                            # From the history card to the version line at the bottom
                            top = page.locator(".card", has=page.locator("h2", has_text="History")).bounding_box()["y"]
                            full = page.evaluate("document.documentElement.scrollHeight")
                            page.screenshot(path=path, full_page=True,
                                            clip={"x": 0, "y": top - 16, "width": width, "height": full - top + 16})
                        else:
                            page.screenshot(path=path)
                        print(path)
                    # The refresh button reloads the page
                    try:
                        with page.expect_navigation(wait_until="load", timeout=10_000):
                            page.click("#refresh", timeout=5_000)
                        check_header(page, problems, f"{where} after refresh", lang, width < 600)
                    except PlaywrightError as err:
                        problems.append(f"{where}: the refresh button does not reload the page: {err.message.splitlines()[0]}")
                    # In Home Assistant's frame: allowed by the page's CSP, same header
                    page.goto(base + "/", wait_until="load")
                    frame = page.frame_locator("iframe")
                    frame.locator("footer.version").wait_for()
                    problems.check(frame.locator("h1").text_content() == w.APP_NAME, f"{where}: no page in the frame")
                    context.close()
                browser.close()
        finally:
            stop(name)
    if problems:
        print("Page check FAILED:", *problems, sep="\n  ", file=sys.stderr)
        raise SystemExit(1)
    print("Page check passed: header, version line, no console errors or CSP violations, light, dark, de, phone width")


if __name__ == "__main__":
    main()
