# NVMe Monitor app for Home Assistant

[![Release](https://img.shields.io/github/v/release/n-schilling/ha-app-nvme-monitor)](https://github.com/n-schilling/ha-app-nvme-monitor/releases)
[![CI](https://github.com/n-schilling/ha-app-nvme-monitor/actions/workflows/ci.yaml/badge.svg?branch=main)](https://github.com/n-schilling/ha-app-nvme-monitor/actions/workflows/ci.yaml)
[![License](https://img.shields.io/github/license/n-schilling/ha-app-nvme-monitor)](LICENSE)
![Home Assistant app](https://img.shields.io/badge/Home%20Assistant-app-41BDF5?logo=homeassistant&logoColor=white)
![Supports aarch64](https://img.shields.io/badge/aarch64-yes-green.svg)
![Supports amd64](https://img.shields.io/badge/amd64-yes-green.svg)

![NVMe Monitor](nvme_monitor/logo.png)

A Home Assistant app (formerly add-on) that publishes the health, wear and temperatures of the host's NVMe drives as sensors through MQTT discovery. Crashes of host programs are covered by the [Crash Guard](https://github.com/n-schilling/ha-app-crash-guard) app.

## Installation

1. Add the repository to your Home Assistant instance:

   [![Add the repository to My Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fn-schilling%2Fha-app-nvme-monitor)

   Or add it manually: **Settings → Apps → Install app → ⋮ → Repositories** (before Home Assistant 2026.2: **Settings → Add-ons → Add-on store**), then paste:

   ```text
   https://github.com/n-schilling/ha-app-nvme-monitor
   ```

2. Open the app from the app store.
3. Install **NVMe Monitor** and start it.

See [the documentation](nvme_monitor/DOCS.md) for the sensors, options, example automations and troubleshooting. Changes are listed in the [changelog](nvme_monitor/CHANGELOG.md).

## Support

Questions, bugs and ideas: [open an issue](https://github.com/n-schilling/ha-app-nvme-monitor/issues/new/choose) in this repository. Security problems: see [SECURITY.md](SECURITY.md).

## License

[Apache License 2.0](LICENSE)
