# NVMe Monitor app for Home Assistant

[![Release](https://img.shields.io/github/v/release/n-schilling/ha-app-nvme-monitor)](https://github.com/n-schilling/ha-app-nvme-monitor/releases)
[![CI](https://github.com/n-schilling/ha-app-nvme-monitor/actions/workflows/ci.yaml/badge.svg?branch=main)](https://github.com/n-schilling/ha-app-nvme-monitor/actions/workflows/ci.yaml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
![Home Assistant app](https://img.shields.io/badge/Home%20Assistant-app-41BDF5?logo=homeassistant&logoColor=white)
![Supports aarch64](https://img.shields.io/badge/aarch64-yes-green.svg)
![Supports amd64](https://img.shields.io/badge/amd64-yes-green.svg)

![NVMe Monitor](nvme_monitor/logo.png)

A Home Assistant app that shows the health, wear, temperatures and history of the host's NVMe drives on a page of its own in Home Assistant, and publishes one Drive problem sensor per drive through MQTT discovery for automations. Crashes of host programs are covered by the [Crash Guard](https://github.com/n-schilling/ha-app-crash-guard) app.

## The web page

The page (**NVMe** in the sidebar, or **Open web UI** on the app's page) looks like Home Assistant's settings and takes over the theme in use. The screenshots show made-up demo data.

| Drive, health and temperatures | History |
|---|---|
| ![Status, SMART values, thermal throttling and temperatures of a drive](docs/screenshots/overview.png) | ![Temperature per hour, data written per day and days on which a counter of trouble grew](docs/screenshots/history.png) |

## Installation

1. Add the repository to your Home Assistant instance:

   [![Add the repository to My Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fn-schilling%2Fha-app-nvme-monitor)

   Or add it manually: **Settings → Apps → Install app → ⋮ → Repositories**, then paste:

   ```text
   https://github.com/n-schilling/ha-app-nvme-monitor
   ```

2. Open the app from the app store.
3. Install **NVMe Monitor** and start it.

See [the documentation](nvme_monitor/DOCS.md) for the web page, the sensor, options, an example automation and troubleshooting. Changes are listed in the [changelog](nvme_monitor/CHANGELOG.md).

## Support

Questions, bugs and ideas: [open an issue](https://github.com/n-schilling/ha-app-nvme-monitor/issues/new/choose) in this repository. Security problems: see [SECURITY.md](SECURITY.md).

## License

[Apache License 2.0](LICENSE)
