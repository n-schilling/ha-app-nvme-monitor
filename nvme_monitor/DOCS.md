# NVMe Monitor

Reads the SMART log of the host's NVMe drives (`/dev/nvme0` to `/dev/nvme3`) and publishes their health, wear and temperatures as Home Assistant sensors through MQTT discovery. Nothing is written to the drives or the host.

Up to version 1.2 this app also counted crashes of host programs; that moved to the [Crash Guard](https://github.com/n-schilling/ha-app-crash-guard) app.

## Requirements

- Home Assistant OS on a host with at least one NVMe drive, e.g. a Raspberry Pi 5 with an NVMe HAT or a PC
- An MQTT broker known to the Supervisor, e.g. the Mosquitto broker app with the MQTT integration

## Sensors

One device per drive: **Host NVMe** for the first one (`nvme0`), **Host NVMe 1** to **Host NVMe 3** for further ones.

| Sensor | Meaning |
|---|---|
| Temperature | Composite temperature |
| Controller temperature | Temperature sensor 2, on many drives the controller; only if the drive has it |
| Temperature sensor 1 | Temperature sensor 1 (diagnostic); only if the drive has it |
| Data written | Total data written, in GB |
| Wear | Percentage used of the rated endurance |
| Available spare | Remaining spare capacity (diagnostic) |
| Media errors | Unrecovered data integrity errors (diagnostic) |
| Error log entries | Entries in the error log (diagnostic) |
| Unsafe shutdowns | Power losses without a clean shutdown (diagnostic) |
| Thermal throttling events | Transitions into thermal throttling |
| Thermal throttling time | Total time throttled (diagnostic) |
| Thermal throttling active | On while throttling grew since the previous reading; attributes carry the drive's warning, critical and throttling thresholds |
| Critical warning | On when the drive reports any critical warning |

## Options

| Option | Default | Meaning |
|---|---|---|
| `interval` | `60` | Seconds between two readings of the SMART log |
| `discovery_prefix` | `homeassistant` | MQTT discovery prefix |
| `log_level` | `info` | How much the app writes to its log: `info`, `warning` or `error` |

## Example automations

Notify when the drive reports a critical warning or its wear passes 80 %:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.host_nvme_critical_warning
    to: "on"
  - trigger: numeric_state
    entity_id: sensor.host_nvme_wear
    above: 80
actions:
  - action: notify.notify
    data:
      message: "NVMe drive needs attention: {{ trigger.to_state.name }} is {{ trigger.to_state.state }}."
```

Notify when the drive throttled for more than ten minutes, a sign that it needs a heatsink or more airflow:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.host_nvme_thermal_throttling_active
    to: "on"
    for: "00:10:00"
actions:
  - action: notify.notify
    data:
      message: "The NVMe drive has been throttling for ten minutes at {{ states('sensor.host_nvme_temperature') }} °C."
```

The entity IDs follow the device names; adjust them if you renamed a device.

## Troubleshooting

- **The app stops with "No readable NVMe drive found".** The host has no NVMe drive, or it is not at `/dev/nvme0` to `/dev/nvme3`. USB enclosures and SATA drives do not provide NVMe SMART data.
- **A drive is missing.** The log lists each drive it skipped. Drives added while the app runs show up after a restart of the app.
- **The entities are unavailable.** The app is stopped, or it lost its connection to the MQTT broker; the log tells which.

## Known limitations

- Up to four NVMe drives.
- Data written is counted by the drive in units of 512 000 bytes and shown in GB.
- Thermal throttling active compares two readings, so it shows throttling one interval late.

## Removing the app

The sensors are announced with retained MQTT messages and stay in Home Assistant after the app is removed. Delete the devices **Host NVMe** (and **Host NVMe 1** to **3**) under **Settings → Devices & services → MQTT**; Home Assistant then removes the retained messages as well.

## Notes

- The app needs `SYS_ADMIN` to read the SMART log through the NVMe admin interface; an AppArmor profile keeps it from writing to the host.
- All entities turn unavailable when the app stops or dies; sensors also expire after three missed intervals.

## Support

Questions, bugs and ideas: open an issue at https://github.com/n-schilling/ha-app-nvme-monitor/issues. Please add the app version and the app log.
