# Changelog

All notable changes to this app are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the app uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
