"""Tests for the web page. Run inside the built image:
  docker run --rm -v "$PWD/tests:/tests:ro" <image> python3 -m unittest discover -s /tests -p 'test_*.py'
The status files are made from the SMART logs in tests/fixtures, as the
monitor service writes them.
"""

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path[:0] = ["/usr/local/lib", str(TESTS.parent / "nvme_monitor/rootfs/usr/local/lib")]

import nvme_web as w  # noqa: E402

NOW = 1_800_000_000.0
HOUR = 3600
DAY = 86400
THRESHOLDS = {"warning_temp_c": 70, "critical_temp_c": 85, "throttle_stage1_c": 78, "throttle_stage2_c": 83}


def smart(name: str = "smart-log.json", **changes) -> dict:
    return {**json.loads((TESTS / "fixtures" / name).read_text()), **changes}


def status(drive: str = "nvme0", at: float = NOW, readable: bool = True, **changes) -> dict:
    return {"drive": drive, "name": "Host NVMe", "model": "Patriot M.2 P300 256GB", "firmware": "H240220a",
            "capacity": 256060514304, "at": at, "readable": readable, "smart": smart(), "thresholds": THRESHOLDS,
            "heat_margin": 10, "throttling": None, "reasons": [], "errors": [], **changes}


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "status"
        self.dir.mkdir()
        self.path = Path(self.tmp.name) / "history.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, value: dict) -> None:
        (self.dir / f"{value['drive']}.json").write_text(json.dumps(value))

    def state(self, now: float = NOW) -> w.State:
        state = w.State(w.History(self.path), self.dir, clock=lambda: now)
        state.refresh()
        return state

    def page(self, state: w.State, lang: str = "en", now: float = NOW) -> str:
        return w.render(state, lang, "a4b1f1df_nvme_monitor", now=now, every=60)


class Problems(unittest.TestCase):
    """The page judges a drive with the same rule as the Drive problem sensor."""

    def test_healthy(self) -> None:
        self.assertEqual(w.problems(status()), [])

    def test_worn_drive_names_every_reason(self) -> None:
        found = w.problems(status(smart=smart("smart-log-worn.json")))
        self.assertEqual([key for key, _ in found], ["media_errors", "spare", "worn"])
        self.assertEqual(dict(found)["spare"], (8, 10))

    def test_critical_warning_and_heat(self) -> None:
        found = w.problems(status(smart=smart("smart-log-throttling.json"),
                                  throttling={"kind": "heat", "detail": "the drive reports a temperature warning"}))
        self.assertEqual([key for key, _ in found], ["warning", "heat"])

    def test_counters_without_heat_are_no_problem(self) -> None:
        self.assertEqual(w.problems(status(throttling={"kind": "counter", "detail": "41"})), [])

    def test_error_log_entries_alone_are_no_problem(self) -> None:
        self.assertEqual(w.problems(status(smart=smart(num_err_log_entries=3))), [])


class History(Fixture):
    def test_lowest_and_highest_temperature_per_hour(self) -> None:
        h = w.History(self.path)
        hour = int(NOW) // HOUR * HOUR
        for at, kelvin in ((hour + 10, 314), (hour + 600, 320), (hour + 1200, 312), (hour + HOUR, 316)):
            h.add("nvme0", status(at=at, smart=smart(temperature=kelvin)))
        self.assertEqual(h.data["nvme0"]["hours"], {str(hour): [39, 47], str(hour + HOUR): [43, 43]})

    def test_counters_as_they_stood_at_the_end_of_the_day(self) -> None:
        h = w.History(self.path)
        h.add("nvme0", status(at=NOW, smart=smart(unsafe_shutdowns=61)))
        h.add("nvme0", status(at=NOW + 60, smart=smart(unsafe_shutdowns=62)))
        day = h.data["nvme0"]["days"][w.local_day(NOW)]
        self.assertEqual((day["unsafe_shutdowns"], day["data_units_written"]), (62, 6538944))
        self.assertNotIn("temperature", day)

    def test_unreadable_drive_adds_nothing(self) -> None:
        h = w.History(self.path)
        h.add("nvme0", status(readable=False))
        self.assertEqual(h.data, {})

    def test_old_hours_and_days_go(self) -> None:
        h = w.History(self.path)
        for i in range(w.DAYS_KEPT + 5):
            h.add("nvme0", status(at=NOW + i * DAY))
        entry = h.data["nvme0"]
        self.assertEqual((len(entry["days"]), len(entry["hours"])), (w.DAYS_KEPT, w.DAYS_KEPT + 5))
        self.assertEqual(min(entry["days"]), w.local_day(NOW + 5 * DAY))
        for i in range(w.HOURS_KEPT):
            h.add("nvme0", status(at=NOW + w.DAYS_KEPT * DAY + i * HOUR))
        self.assertEqual(len(h.data["nvme0"]["hours"]), w.HOURS_KEPT)

    def test_kept_across_restarts(self) -> None:
        h = w.History(self.path)
        h.add("nvme0", status())
        h.save(NOW)
        self.assertEqual(w.History(self.path).data, h.data)

    def test_broken_store_starts_empty(self) -> None:
        self.path.write_text("{")
        self.assertEqual(w.History(self.path).data, {})
        self.path.write_text("[]")
        self.assertEqual(w.History(self.path).data, {})

    def test_saved_at_most_every_five_minutes(self) -> None:
        h = w.History(self.path)
        h.add("nvme0", status())
        h.maybe_save(NOW)
        h.add("nvme0", status(at=NOW + 60, smart=smart(unsafe_shutdowns=99)))
        h.maybe_save(NOW + 60)
        self.assertEqual(w.History(self.path).data["nvme0"]["days"][w.local_day(NOW)]["unsafe_shutdowns"], 61)
        h.maybe_save(NOW + w.SAVE_EVERY)
        self.assertEqual(w.History(self.path).data["nvme0"]["days"][w.local_day(NOW)]["unsafe_shutdowns"], 99)


class Status(Fixture):
    def test_reads_every_drive(self) -> None:
        self.write(status())
        self.write(status("nvme1", name="Host NVMe 1"))
        self.assertEqual(sorted(self.state().drives), ["nvme0", "nvme1"])

    def test_broken_and_foreign_files_are_skipped(self) -> None:
        (self.dir / "nvme0.json").write_text("{")
        (self.dir / "nvme1.json").write_text(json.dumps({"drive": "nvme1"}))
        (self.dir / "nvme2.json.tmp").write_text(json.dumps(status("nvme2")))
        self.assertEqual(self.state().drives, {})

    def test_each_reading_counts_once(self) -> None:
        self.write(status(smart=smart(temperature=330)))
        state = self.state()
        # The monitor stopped: the same reading again, here with a changed value
        self.write(status(smart=smart(temperature=300)))
        state.refresh()
        hours = state.history.data["nvme0"]["hours"]
        self.assertEqual(list(hours.values()), [[57, 57]])


class Page(Fixture):
    def test_healthy_drive(self) -> None:
        self.write(status())
        page = self.page(self.state())
        self.assertIn("The drive is healthy", page)
        for text in ("Patriot M.2 P300 256GB", "256 GB", "3.35 TB", "61", "41 °C", "64 °C", "70 °C", "78 °C",
                     "252,869 transitions", "3 h 30 min"):
            self.assertIn(text, page)
        # One drive: no heading with its name
        self.assertNotIn("<h2>Host NVMe</h2>", page)

    def test_german(self) -> None:
        self.write(status())
        page = self.page(self.state(), "de")
        for text in ('<html lang="de">', "Das Laufwerk ist in Ordnung", "3,35 TB", "252.869 Übergänge",
                     "Unsaubere Abschaltungen", "Gesamt", "41 °C"):
            self.assertIn(text, page)

    def test_problem_with_its_reasons(self) -> None:
        self.write(status(smart=smart("smart-log-worn.json")))
        page = self.page(self.state())
        self.assertIn("The drive needs attention", page)
        for text in ("3 media errors", "Spare at 8 %, threshold 10 %", "Rated endurance used up (104 %)"):
            self.assertIn(text, page)

    def test_critical_warning_bits_by_name(self) -> None:
        self.write(status(smart=smart(critical_warning=6)))
        page = self.page(self.state(), "de")
        self.assertIn("Kritische Warnung: Temperatur, Zuverlässigkeit gesunken", page)

    def test_throttling_states(self) -> None:
        self.write(status(throttling={"kind": "heat", "detail": "at 60 °C, threshold 70 °C"}))
        self.assertIn("Throttling: at 60 °C, threshold 70 °C", self.page(self.state()))
        self.write(status(throttling={"kind": "counter", "detail": "41"}))
        page = self.page(self.state(), "de")
        self.assertIn("Zähler steigen bei 41 °C ohne Hitze", page)
        self.assertIn("höchstens 10 °C unter", page)

    def test_unreadable_drive(self) -> None:
        self.write(status(readable=False))
        self.assertIn("SMART log not readable since", self.page(self.state()))

    def test_monitor_stopped(self) -> None:
        self.write(status(at=NOW - 600))
        self.assertIn("No reading for 10 min: the monitor does not run", self.page(self.state()))
        self.write(status(at=NOW - 120))
        self.assertNotIn("the monitor does not run", self.page(self.state()))

    def test_error_log(self) -> None:
        errors = [{"error_count": 2, "sqid": 1, "cmdid": 12, "status_field": 8194, "lba": 4096, "nsid": 1}]
        self.write(status(errors=errors))
        page = self.page(self.state())
        self.assertIn("<h2>Error log</h2>", page)
        self.assertIn("0x2002", page)
        self.write(status())
        self.assertNotIn("<h2>Error log</h2>", self.page(self.state()))

    def test_values_are_escaped(self) -> None:
        self.write(status(model='<script>alert("x")</script>'))
        page = self.page(self.state())
        self.assertNotIn('<script>alert("x")', page)
        self.assertIn("&lt;script&gt;", page)

    def test_several_drives_have_headings(self) -> None:
        self.write(status())
        self.write(status("nvme1", name="Host NVMe 1"))
        page = self.page(self.state())
        self.assertIn("<h2>Host NVMe</h2>", page)
        self.assertIn("<h2>Host NVMe 1</h2>", page)

    def test_no_drive_yet(self) -> None:
        state = w.State(w.History(self.path), self.dir, clock=lambda: NOW)
        self.assertIn("Waiting for the first reading", self.page(state))
        state.refresh()
        self.assertIn("No readable NVMe drive.", self.page(state))

    def test_settings_link_leaves_the_frame(self) -> None:
        self.write(status())
        self.assertIn('href="/config/app/a4b1f1df_nvme_monitor/config" target="_top"', self.page(self.state()))


class Charts(Fixture):
    def history(self, days: int, written_per_day: int = 2000, errors_on: int | None = None) -> w.State:
        state = self.state()
        for i in range(days):
            at = NOW - (days - 1 - i) * DAY
            changes = {"data_units_written": 6_000_000 + i * written_per_day,
                       "media_errors": 1 if errors_on is not None and i >= errors_on else 0,
                       "temperature": 314 + i % 5}
            state.history.add("nvme0", status(at=at, smart=smart(**changes)))
        return state

    def test_empty_history(self) -> None:
        self.write(status(readable=False))
        self.assertIn("The history begins with the first reading", self.page(self.state()))

    def test_temperature_band_and_written_bars(self) -> None:
        self.write(status())
        page = self.page(self.history(40))
        self.assertIn("Temperature, lowest and highest per hour, last 30 days", page)
        self.assertIn('class="band"', page)
        # 1.02 GB a day: one bar per day after the first, the chart keeps 90 days
        self.assertEqual(page.count('class="bar"'), 39)
        self.assertIn("1.02 GB</title>", page)

    def test_a_day_without_writes_has_no_bar_but_a_tooltip(self) -> None:
        self.write(status())
        page = self.page(self.history(3, written_per_day=0))
        self.assertEqual(page.count('class="bar"'), 0)
        self.assertIn("· 0 kB</title>", page)

    def test_written_chart_keeps_ninety_days(self) -> None:
        self.write(status())
        self.assertEqual(self.page(self.history(120)).count('class="bar"'), w.WRITTEN_DAYS)

    def test_days_on_which_a_counter_of_trouble_grew(self) -> None:
        self.write(status())
        page = self.page(self.history(10, errors_on=7))
        self.assertIn("Media errors: 0 → 1", page)
        self.assertEqual(page.count("Media errors: 0 → 1"), 1)
        page = self.page(self.history(10))
        self.assertIn("No counter of trouble grew since the history began.", page)

    def test_ticks_are_round(self) -> None:
        self.assertEqual(w.ticks(38, 49), [38, 40, 42, 44, 46, 48])
        self.assertEqual(w.ticks(0, 1.3e9), [0, 5e8, 1e9])


class Format(unittest.TestCase):
    def test_sizes(self) -> None:
        en, de = w.Format("en"), w.Format("de")
        self.assertEqual(en.size(3_858_190_000_000), "3.86 TB")
        self.assertEqual(de.size(3_858_190_000_000), "3,86 TB")
        self.assertEqual(en.size(256_060_514_304), "256 GB")
        self.assertEqual(de.size(12_345_678_901_234), "12,3 TB")
        self.assertEqual(en.size(5000), "5 kB")

    def test_numbers(self) -> None:
        self.assertEqual(w.Format("de").int(270496), "270.496")
        self.assertEqual(w.Format("en").int(270496), "270,496")
        self.assertEqual(w.Format("de").dec(1234.5, 1), "1.234,5")

    def test_language(self) -> None:
        self.assertEqual(w.lang_of("de-DE,de;q=0.9,en;q=0.8"), "de")
        self.assertEqual(w.lang_of("en-US,de;q=0.5"), "en")
        self.assertEqual(w.lang_of(None), "en")


class Server(Fixture):
    def serve(self, allowed: set[str]) -> str:
        self.write(status())
        server = w.Server(("127.0.0.1", 0), w.handler(self.state(time.time()), None, allowed))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def get(self, url: str, method: str = "GET", **headers) -> tuple[int, str]:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method=method, headers=headers)) as answer:
                return answer.status, answer.read().decode()
        except urllib.error.HTTPError as err:
            with err:
                return err.code, err.read().decode()

    def test_only_the_ingress_proxy(self) -> None:
        self.assertEqual(self.get(self.serve({w.INGRESS_PEER}) + "/"), (403, "only through Home Assistant"))

    def test_page_in_the_browsers_language(self) -> None:
        base = self.serve(set())
        code, page = self.get(base + "/", **{"Accept-Language": "de-DE,de;q=0.9"})
        self.assertEqual(code, 200)
        self.assertIn("Das Laufwerk ist in Ordnung", page)
        self.assertEqual(self.get(base + "/other")[0], 404)
        self.assertEqual(self.get(base + "/../../data/options.json")[0], 404)

    def test_read_only(self) -> None:
        base = self.serve(set())
        for method in ("POST", "PUT", "DELETE"):
            self.assertEqual(self.get(base + "/", method)[0], 501)


if __name__ == "__main__":
    unittest.main()
