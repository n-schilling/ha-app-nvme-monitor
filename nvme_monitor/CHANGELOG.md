# Changelog

All notable changes to this app are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the app uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.1.1] - 2026-11-11

### Security

- The web page no longer gets the Supervisor token
- A narrower AppArmor profile: only the capabilities, networks, programs and paths the app uses, measured on Home Assistant OS before it was enforced

## [3.1.0] - 2026-11-10

### Added

- The web page shows the app's name and version at the bottom
- Days without a reading are visible in the history: data written over a gap is spread evenly over its days as lighter bars, with the whole amount in the tooltip, and a counter of trouble that grew in a gap is listed with the whole gap instead of the day after it

### Changed

- The web page runs as a user of its own, without root and without the `SYS_ADMIN` capability the monitor needs to read the drives; its history moves to `/data/web` when the app starts
- The MQTT broker's address and credentials are no longer on the command line of the mosquitto clients, where other processes could read the password, but in their configuration files, readable by root only; a broker that asks for TLS gets it
- The web page sends security headers (`X-Content-Type-Options`, a content security policy)
- A drive whose SMART log does not answer at the start is monitored all the same and read again every interval; before, it was skipped until the app restarted
- Without any NVMe drive or MQTT service the app stops with a fatal error in its log, instead of restarting every second
- Requires Home Assistant 2026.11 or later; the documentation follows its terms, with an overview, the flow of a reading and the states of the sensor as diagrams, and new screenshots
- The web page uses the stylesheet all apps of this author share, closer to Home Assistant's own look
- The base image is pinned by version and digest, so a moved tag cannot change the app unnoticed; Dependabot proposes updates
- The README explains how to work on the app: where the code is, how to run the checks and how a release is made
- Built on `home-assistant/base-debian:trixie-2026.11.0`, pinned, so a rebuild gives the same image; Dependabot proposes new versions

### Fixed

- A drive that could not be read lost its error log entries and throttling state on the page, just when they would explain the failure; the page now keeps those of the last reading
- "No reading: the monitor does not run" did not show for a drive that was not readable
- A history file with entries in an unexpected form stopped the history from growing; such entries are now left out with one warning
- Stopping the app could take up to 10 seconds while the MQTT broker was away
- A compiled Python file that does not belong to the app was part of the repository and the image

## [3.0.0] - 2026-11-02

### Added

- A web page in the sidebar (ingress, administrators only, read only) with the whole SMART log of every drive: health, wear, spare, errors, unsafe shutdowns, the critical warning by name, all temperature sensors and thresholds, throttling, data read and written, power-on hours and power cycles, and the drive's error log when it has entries
- A history on that page, kept in the app's data: lowest and highest temperature per hour for 30 days, data written per day for 90 days, and the days on which media errors, error log entries, unsafe shutdowns or wear grew

### Changed

- **Breaking:** Home Assistant gets one entity per drive, **Drive problem**. The 13 other sensors (temperatures, data written, wear, available spare, media errors, error log entries, unsafe shutdowns, thermal throttling events, time and active, critical warning) are removed with their retained states; their values are on the web page. Automations and dashboards that use them need to change, and their recorded history stays in Home Assistant until you delete it
- Drive problem also turns on for thermal throttling the temperatures explain, with the reason `thermal throttling, …`
- The image contains Python 3 for the web page

## [2.3.0] - 2026-11-01

### Changed

- Thermal throttling active turns on only when the drive also shows heat: a temperature warning, time above its warning or critical temperature, or a composite temperature at most 10 °C below its lowest threshold. Some firmware, for example on the Patriot P300, lets the throttling counters grow at 40 °C, 60 °C below its throttling threshold; that was reported as throttling and logged as a warning every minute
- Counters that grow without heat set the new attribute `counters_grew_without_heat` and are logged once per episode as info
- Real throttling is logged once as a warning with its reason when it starts and once when it ends, instead of a warning on every reading

## [2.2.5] - 2026-10-27

### Changed

- Rebuilt on the current `home-assistant/base-debian:trixie` (2026.10.3): the Debian system packages and the packages the app installs are upgraded to their latest security updates; s6-overlay and bashio are unchanged

## [2.2.4] - 2026-10-19

### Fixed

- The app no longer stops when the MQTT broker is away, for example during its restart: it logs one warning with the reason and one line once the broker answers again, instead of an `Error: …` line for every message; a round stops at its first message the broker does not take
- A message the broker did not take was reported as `Reading nvme0 failed`

### Changed

- The temperature thresholds are sent with every round, like the availability, in case the broker lost its retained messages

## [2.2.3] - 2026-10-11

### Changed

- The base image is set in the Dockerfile only (multi-arch `base-debian:trixie`, unchanged contents); the deprecated `build.yaml` is gone, so the Supervisor no longer warns about it when building the app

## [2.2.2] - 2026-10-09

### Changed

- The log no longer starts with s6-overlay's warning that user bundles in `s6-rc.d` are deprecated; the service is now registered in `user-bundles.d`. The Debian base image ships s6-overlay 3.2.3.2 since the last build

## [2.2.1] - 2026-10-09

### Changed

- s6-overlay logs only warnings and errors, so the start and stop of the app no longer fill the log with `s6-rc: info` lines

## [2.2.0] - 2026-10-07

### Added

- *Drive problem*: one binary sensor per drive for automations, on for a critical warning, media errors, the spare at or below the drive's threshold, or the rated endurance used up; the `reasons` attribute names them

## 2.1.1 - 2026-10-07

### Fixed

- Moving to device discovery stopped on the empty lines between old discovery messages that end in a newline

## 2.1.0 - 2026-10-07

### Added

- Option `log_level`: `info`, `warning` or `error`

### Changed

- Device based MQTT discovery: one retained message per drive announces it with all its entities. The entities announced one by one before are handed over with their entity IDs and history, and their old topics are removed

## 2.0.0 - 2026-10-07

**Breaking: the app is now NVMe Monitor and covers the NVMe drives only.** Install Crash Guard 1.2.0 or later to keep the host program crash sensors with their entity IDs.

### Removed

- The host program crash sensors (*Program crashes since boot*, *Program crashes (window)*) and the option `crash_window_hours`; they moved to the Crash Guard app, and this app removes its crash entities
- Access to the host journal

## 1.2.1 - 2026-10-07

### Fixed

- Clearing the old crash topics stopped the app in a restart loop (mosquitto_pub refuses an empty message on stdin)

## 1.2.0 - 2026-10-07

### Added

- Up to four NVMe drives (`nvme0` to `nvme3`), one device each; the first keeps its entity IDs
- Entities turn unavailable when the app stops or dies (MQTT availability with a last will)
- Discovery names the app, its version and support URL (origin); the devices link to the app page
- AppArmor profile
- Icon, logo, German translation of the options
- Documentation: example automations, troubleshooting, known limitations, removing the app

### Changed

- Host crash states moved to the topics of the host device; the old retained topics are cleared
- A failing reading is logged once per outage instead of on every interval

### Fixed

- Temperature sensors a drive does not have are no longer created; before, a missing controller sensor stayed unknown
- Unset throttling thresholds are left out of the attributes instead of showing -273 °C

## 1.1.1 - 2026-10-06

### Added

- Tests and CI; the NVMe sysfs path can be overridden for the tests (no change in behaviour)

### Changed

- config.yaml no longer repeats the default boot option

## 1.1.0 - 2026-10-06

### Added

- Host board and NVMe model detected instead of fixed values
- amd64 support
- Documentation

### Changed

- English texts throughout

## 1.0.5 - 2026-10-06

### Added

- First version in this repository

[2.2.0]: https://github.com/n-schilling/ha-app-nvme-monitor/releases/tag/v2.2.0
