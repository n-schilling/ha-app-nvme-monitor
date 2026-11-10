# NVMe Monitor app for Home Assistant

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
![Home Assistant app](https://img.shields.io/badge/Home%20Assistant-app-41BDF5?logo=homeassistant&logoColor=white)
![Supports aarch64](https://img.shields.io/badge/aarch64-yes-green.svg)
![Supports amd64](https://img.shields.io/badge/amd64-yes-green.svg)

A Home Assistant app that reads the SMART log of the host's NVMe drives and shows their health, wear, temperatures, thermal throttling and history on a web page in Home Assistant. For automations it publishes one **Drive problem** sensor per drive through MQTT discovery, which turns on when a drive needs attention and names the reasons. It only reads: nothing is written to the drives or the host. Crashes of host programs are covered by the [Crash Guard](https://github.com/n-schilling/ha-app-crash-guard) app.

Requires Home Assistant 2026.11 or later.

## Screenshots

The data in these pictures is made up for these pictures.

<img src="docs/screenshots/overview.png" alt="The web page: the drive is healthy, with model, capacity, data read and written, health values, thermal throttling and all temperatures" width="700">

<img src="docs/screenshots/history.png" alt="The history on the web page: temperature per hour for 30 days, data written per day for 90 days with a gap shown as lighter bars, the days on which a counter of trouble grew, and the app's version at the bottom" width="700">

<img src="docs/screenshots/phone-dark.png" alt="The web page on a phone in dark mode" width="300">

## Overview

```mermaid
flowchart LR
    drives[("NVMe drives<br>/dev/nvme0 to nvme3")]
    subgraph app["NVMe Monitor app"]
        monitor["Monitor<br>(root, SYS_ADMIN)"]
        status[("Status file<br>per drive")]
        web["Web page<br>(own user, no capabilities)"]
        history[("History<br>/data/web")]
        monitor --> status --> web
        web <--> history
    end
    drives -- "SMART log, error log,<br>every interval" --> monitor
    monitor -- "MQTT discovery<br>and states" --> broker["MQTT broker"]
    broker --> sensor["Drive problem<br>binary sensor"]
    sensor --> automations["Your automations"]
    web -- "ingress" --> ui["Open web UI<br>in Home Assistant"]
```

The monitor reads every drive once per interval, judges whether it needs attention and sends that to Home Assistant; the whole SMART log goes to the web page, which keeps its own history. The [documentation](nvme_monitor/DOCS.md) has the details, with the flow of a reading and the states of the sensor.

## Installation

1. Add the repository to your Home Assistant instance:

   [![Add the repository to My Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fn-schilling%2Fha-app-nvme-monitor)

   Or add it manually: **Settings → Apps → Install app → ⋮ → Repositories**, then paste:

   ```text
   https://github.com/n-schilling/ha-app-nvme-monitor
   ```

2. Find **NVMe Monitor** in the list of apps, install it and start it.
3. Open the web page with **Open web UI** on the app's page.

See [the documentation](nvme_monitor/DOCS.md) for the options, the web page, the sensor, an example automation and troubleshooting. Changes are listed in the [changelog](nvme_monitor/CHANGELOG.md).

## Support

Questions, bugs and ideas: [open an issue](https://github.com/n-schilling/ha-app-nvme-monitor/issues/new/choose) in this repository. Security problems: see [SECURITY.md](SECURITY.md).

## License

[Apache License 2.0](LICENSE)
