#!/bin/bash
# Tests the NVMe monitor against a real Mosquitto broker: device
# discovery, retained states, the hand-over of single entity topics, the stop
# and the last will. Run inside the built image, next to a broker reachable as
# "broker" (see the CI workflow):
#   docker run --rm --network <net> -v "$PWD/tests:/tests:ro" <image> bash /tests/test_broker.sh
# nvme is a stub; mosquitto_pub and mosquitto_sub are the real ones.
set -u

readonly RUN=/etc/s6-overlay/s6-rc.d/monitor/run
readonly DEVICE=homeassistant/device/nvme_monitor/config
readonly AVAIL=nvme_monitor/availability
WORK=$(mktemp -d)
FAILED=0
export SMART_LOG=/tests/fixtures/smart-log.json NVME_SYSFS="${WORK}/sysfs"
mkdir -p "${NVME_SYSFS}/nvme0"
printf 'Patriot M.2 P300 256GB\n' > "${NVME_SYSFS}/nvme0/model"
printf 'H240220a\n' > "${NVME_SYSFS}/nvme0/firmware_rev"
touch /dev/nvme0

expect() {
    local name=$1 got=$2 want=$3
    if [[ "${got}" == "${want}" ]]; then
        echo "ok    ${name}"
    else
        echo "FAIL  ${name}: got '${got}', want '${want}'"
        FAILED=1
    fi
}

# Retained payload of a topic, empty when there is none
retained() { mosquitto_sub -h broker -t "$1" --retained-only -W 2 -C 1 2>/dev/null; }
# Kills every process of the service at once, as when the container dies:
# the service, its availability connection and that connection's loop
kill_all() {
    local p cmd
    for p in /proc/[0-9]*; do
        cmd=$(tr '\0' ' ' < "${p}/cmdline" 2>/dev/null) || continue
        if [[ "${cmd}" == *"${RUN}"* || "${cmd}" == mosquitto_sub*-availability* ]]; then
            kill -9 "${p#/proc/}" 2>/dev/null
        fi
    done
}
# Waits up to $2 s for a command to succeed
wait_for() { local i; for ((i = 0; i < $2 * 5; i++)); do eval "$1" && return 0; sleep 0.2; done; return 1; }

# Starts the monitor in the background with the stub nvme
start_service() {
    PATH="/tests/stubs/nvme-only:${PATH}" bash -c '
        source /usr/lib/bashio/bashio.sh
        bashio::config() {
            case "$1" in
                interval) echo 1 ;;
                discovery_prefix) echo homeassistant ;;
                *) echo null ;;
            esac
        }
        bashio::services.available() { return 0; }
        bashio::services() {
            case "$2" in
                host) echo broker ;;
                port) echo 1883 ;;
                *) echo "" ;;
            esac
        }
        source "$0"
    ' "${RUN}" >> "${WORK}/log" 2>&1 &
    SERVICE=$!
}

echo '# Hand-over from single entity discovery'
# As versions before 2.1.0 left them: jq output with its trailing newline
for e in temp_composite percent_used; do
    jq -nc --arg u "nvme_monitor_${e}" '{unique_id:$u}' \
        | mosquitto_pub -h broker -r -t "homeassistant/sensor/nvme_monitor/${e}/config" -s
done
start_service
wait_for '[[ -n "$(retained nvme_monitor/temp_composite)" ]]' 90
expect 'device message retained' "$(retained "${DEVICE}" | jq '.components | length')" 14
expect 'old topics removed' "$(retained homeassistant/sensor/nvme_monitor/temp_composite/config)$(retained homeassistant/sensor/nvme_monitor/percent_used/config)" ''
expect 'hand-over logged' "$(grep -c '2 entities of nvme_monitor moved to device discovery' "${WORK}/log")" 1
expect 'state retained' "$(retained nvme_monitor/temp_composite)" 41
expect 'online' "$(retained "${AVAIL}")" online

echo '# Regular stop'
kill -TERM "${SERVICE}"
wait "${SERVICE}"
expect 'offline after a stop' "$(retained "${AVAIL}")" offline

echo '# Last will'
start_service
wait_for '[[ "$(retained "${AVAIL}")" == online ]]' 90
# The app dies without saying goodbye: the broker sends the last will
kill_all
wait_for '[[ "$(retained "${AVAIL}")" == offline ]]' 10
expect 'offline through the last will' "$(retained "${AVAIL}")" offline
expect 'second start found nothing to hand over' "$(grep -c 'moved to device discovery' "${WORK}/log")" 1

if (( FAILED )); then
    echo '--- service log:'
    cat "${WORK}/log"
    exit 1
fi
echo 'All tests passed'
