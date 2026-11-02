# NVMe Monitor

Reads the SMART log of the host's NVMe drives (`/dev/nvme0` to `/dev/nvme3`) and shows it on a web page in the sidebar: health, wear, temperatures, throttling, data written and how they changed. Home Assistant gets one **Drive problem** sensor per drive through MQTT discovery, for automations and notifications. Nothing is written to the drives or the host.

Up to version 1.2 this app also counted crashes of host programs; that moved to the [Crash Guard](https://github.com/n-schilling/ha-app-crash-guard) app.

## Requirements

- Home Assistant OS on a host with at least one NVMe drive, e.g. a Raspberry Pi 5 with an NVMe HAT or a PC
- An MQTT broker known to the Supervisor, e.g. the Mosquitto broker app with the MQTT integration

## Web page

Open **NVMe** in the sidebar (or **Open web UI** on the app page; to show it in the sidebar, turn on **Show in sidebar** there). Only administrators can open it, and it only reads. It follows the browser's language (English or German) and Home Assistant's theme, and updates every 30 seconds.

For every drive it shows:

| Card | Content |
|---|---|
| Drive | Whether the drive needs attention and why, model, firmware, capacity, hours powered on, power cycles, controller busy time, data read and written with the number of commands |
| Health | Wear, available spare and its threshold, media errors, error log entries, unsafe shutdowns, the critical warning by name |
| Temperatures | Composite temperature, every temperature sensor the drive has, its warning, critical and throttling thresholds, time above the warning and critical temperature |
| Thermal throttling | Whether the drive throttles from heat now, or only its counters grow (see below), the counters and times of both stages |
| History | Lowest and highest temperature per hour for 30 days, data written per day for 90 days, and the days on which media errors, error log entries, unsafe shutdowns or wear grew |
| Error log | The entries of the drive's error log, only when it has some |

The drive keeps its counters itself, but no history. The page keeps one in the app's data, for 30 days by the hour and a year by the day; it begins with the first reading after installing or updating to 3.0.0. A message below the status says when the SMART log could not be read or the monitor stopped reading.

## Sensor

One device per drive: **Host NVMe** for the first one (`nvme0`), **Host NVMe 1** to **Host NVMe 3** for further ones, each with one entity:

| Entity | Meaning |
|---|---|
| Drive problem | On when the drive needs attention: a critical warning, media errors, the spare at or below the drive's threshold, the rated endurance used up, or thermal throttling the temperatures explain (see below); the `reasons` attribute names them |

Up to version 2.3 the app also published the values as sensors (temperatures, data written, wear, spare, errors, unsafe shutdowns, throttling); since 3.0.0 they are on the web page, and the app removes those entities and their retained states when it starts.

## Options

| Option | Default | Meaning |
|---|---|---|
| `interval` | `60` | Seconds between two readings of the SMART log |
| `discovery_prefix` | `homeassistant` | MQTT discovery prefix |
| `log_level` | `info` | How much the app writes to its log: `info`, `warning` or `error` |

## Example automation

Notify when a drive needs attention, with the reasons:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.host_nvme_drive_problem
    to: "on"
actions:
  - action: notify.notify
    data:
      message: >-
        NVMe drive needs attention: {{ state_attr('binary_sensor.host_nvme_drive_problem', 'reasons') | join(', ') }}
```

The entity IDs follow the device names; adjust them if you renamed a device.

## Troubleshooting

- **The app stops with "No readable NVMe drive found".** The host has no NVMe drive, or it is not at `/dev/nvme0` to `/dev/nvme3`. USB enclosures and SATA drives do not provide NVMe SMART data.
- **A drive is missing.** The log lists each drive it skipped. Drives added while the app runs show up after a restart of the app.
- **The entities are unavailable.** The app is stopped, or it lost its connection to the MQTT broker; the log tells which.
- **The web page says the monitor does not run.** The page gets no new readings; the app log tells why.

## Known limitations

- Up to four NVMe drives.
- Data written is counted by the drive in units of 512 000 bytes and shown in GB.
- Throttling compares two readings, so it shows one interval late.
- Some firmware counts more than thermal throttling in the throttling counters, so they grow at normal temperatures. Throttling therefore counts only when the drive also shows heat: a temperature warning, time above its warning or critical temperature, or a composite temperature at most 10 °C below its lowest threshold in one of the two readings. Without any thresholds from the drive the counters alone count. Counters that grow without heat are no drive problem; the web page says so, shows the raw counters, and the log has one info line per episode.
- The history of the web page starts empty; the drive's counters before it are only shown as totals.
- Throttling is logged once as a warning when it starts and once when it ends.

## Removing the app

The sensors are announced with retained MQTT messages and stay in Home Assistant after the app is removed. Delete the devices **Host NVMe** (and **Host NVMe 1** to **3**) under **Settings → Devices & services → MQTT**; Home Assistant then removes the retained messages as well. The history of the web page goes with the app's data.

## Notes

- The app needs `SYS_ADMIN` to read the SMART log through the NVMe admin interface; an AppArmor profile keeps it from writing to the host.
- The web page answers only Home Assistant's ingress proxy and only reads; it has no buttons that change anything. It shows no serial number.
- The entities turn unavailable when the app stops or dies; they also expire after three missed intervals.

## Support

Questions, bugs and ideas: open an issue at https://github.com/n-schilling/ha-app-nvme-monitor/issues. Please add the app version and the app log.
