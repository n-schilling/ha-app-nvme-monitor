#!/bin/bash
# Tests for the monitor service. Run inside the built image:
#   docker run --rm -v "$PWD/tests:/tests:ro" <image> bash /tests/test_monitor.sh
# nvme and mosquitto_pub/_sub are stubs answering from tests/fixtures;
# bashio's Supervisor lookups are replaced by fixed values.
set -u

readonly RUN=/etc/s6-overlay/s6-rc.d/monitor/run
WORK=$(mktemp -d)
export PUBLISHED="${WORK}/published" NVME_SYSFS="${WORK}/sysfs" PATH="/tests/stubs:${PATH}" RETAINED="${WORK}/retained"
: > "${RETAINED}"
FAILED=0

# nvme0 and nvme1 are readable; nvme2 has no device node in the container
for d in nvme0 nvme1 nvme2; do
    mkdir -p "${NVME_SYSFS}/${d}"
    printf 'Patriot M.2 P300 256GB                  \n' > "${NVME_SYSFS}/${d}/model"
    printf 'H240220a\n' > "${NVME_SYSFS}/${d}/firmware_rev"
done
printf 'Basic NVMe 1TB\n' > "${NVME_SYSFS}/nvme1/model"
touch /dev/nvme0 /dev/nvme1

# Runs the service until its loop pauses for the second time. Between the two
# readings the SMART log switches to $1, so the second one sees a change.
run_monitor() {
    export SMART_LOG="${WORK}/smart-log.json" SECOND="$1"
    cp /tests/fixtures/smart-log.json "${SMART_LOG}"
    : > "${PUBLISHED}"
    bash -c '
        source /usr/lib/bashio/bashio.sh
        bashio::config() {
            case "$1" in
                interval) echo 60 ;;
                discovery_prefix) echo homeassistant ;;
            esac
        }
        bashio::services.available() { return 0; }
        bashio::services() {
            case "$2" in
                host) echo core-mosquitto ;;
                port) echo 1883 ;;
                username) echo addons ;;
                password) echo secret ;;
            esac
        }
        sleep() { return 0; }
        pauses=0
        pause() {
            pauses=$((pauses + 1))
            (( pauses >= 2 )) && exit 0
            cp "${SECOND}" "${SMART_LOG}"
        }
        source '"${RUN}"'
    ' > "${WORK}/log" 2>&1
}

# Last payload published to a topic
last() { awk -F '\t' -v t="$1" '$1 == t { v = $2 } END { print v }' "${PUBLISHED}"; }

expect() {
    local name=$1 got=$2 want=$3
    if [[ "${got}" == "${want}" ]]; then
        echo "ok    ${name}"
    else
        echo "FAIL  ${name}: got '${got}', want '${want}'"
        FAILED=1
    fi
}

echo '# Readings'
run_monitor /tests/fixtures/smart-log.json
expect 'composite temperature from Kelvin' "$(last nvme_monitor/temp_composite)" 41
expect 'controller temperature' "$(last nvme_monitor/temp_sensor2)" 64
expect 'data written in GB' "$(last nvme_monitor/data_written)" 3347.94
expect 'wear' "$(last nvme_monitor/percent_used)" 2
expect 'unsafe shutdowns' "$(last nvme_monitor/unsafe_shutdowns)" 61
expect 'throttling events, both stages' "$(last nvme_monitor/throttle_count)" 252869
expect 'no throttling without a change' "$(last nvme_monitor/throttle_active)" OFF
expect 'no critical warning' "$(last nvme_monitor/critical_warning)" OFF
expect 'no drive problem' "$(last nvme_monitor/health_problem)|$(last nvme_monitor/health_problem/attr)" 'OFF|{"reasons":[]}'
expect 'thresholds from the controller' "$(last nvme_monitor/throttle_active/attr)" \
    '{"warning_temp_c":70,"critical_temp_c":85,"throttle_stage1_c":78,"throttle_stage2_c":83}'

echo '# Second drive without extra temperature sensors'
dev() { last "homeassistant/device/$1/config"; }
expect 'its own topics' "$(last nvme_monitor_nvme1/temp_composite)" 27
expect 'no state for a sensor it lacks' "$(grep -c '^nvme_monitor_nvme1/temp_sensor' "${PUBLISHED}")" 0
expect 'no entity for a sensor it lacks' "$(dev nvme_monitor_nvme1 | jq -c '.components | has("temp_sensor1") or has("temp_sensor2")')" false
expect 'its own device' \
    "$(dev nvme_monitor_nvme1 | jq -r '.components.temp_composite.unique_id + " " + .device.name + " " + .device.model')" \
    'nvme_monitor_nvme1_temp_composite Host NVMe 1 Basic NVMe 1TB'
expect 'unset throttling stages are left out' "$(last nvme_monitor_nvme1/throttle_active/attr)" \
    '{"warning_temp_c":70,"critical_temp_c":85}'
expect 'unreadable drive skipped' "$(grep -c 'Skipping nvme2' "${WORK}/log")" 1

echo '# Crash sensors moved to Crash Guard'
expect 'old crash entities removed' "$(grep -c '^homeassistant/sensor/ha_host/crashes_[a-z]*/config	$' "${PUBLISHED}")" 2
expect 'old crash states removed' "$(last ha_host/crashes_window)|$(last nvme_monitor/crashes_window)" '|'
expect 'no crash entity announced' "$(grep -c 'crashes.*/config	{' "${PUBLISHED}")" 0

echo '# Discovery'
expect 'one message per drive' "$(grep -c '^homeassistant/device/[a-z0-9_]*/config	{' "${PUBLISHED}")" 2
expect 'all entities of both drives: 13 + 11' \
    "$(( $(dev nvme_monitor | jq '.components | length') + $(dev nvme_monitor_nvme1 | jq '.components | length') ))" 26
expect 'no single entity topics' "$(grep -c '^homeassistant/[a-z_]*/nvme_monitor[a-z0-9_]*/[a-z0-9_]*/config	{' "${PUBLISHED}")" 0
expect 'unique ID unchanged (entity IDs depend on it)' \
    "$(dev nvme_monitor | jq -r .components.temp_composite.unique_id)" nvme_monitor_temp_composite
expect 'NVMe model from sysfs, trimmed' \
    "$(dev nvme_monitor | jq -r '.device.model + " " + .device.sw_version')" 'Patriot M.2 P300 256GB H240220a'
expect 'sensors expire after three intervals' "$(dev nvme_monitor | jq -r .components.temp_composite.expire_after)" 180
expect 'availability for the whole device' "$(dev nvme_monitor | jq -r .availability_topic)" nvme_monitor/availability
expect 'origin names the app and its support URL' \
    "$(dev nvme_monitor | jq -r '.origin.support_url')" https://github.com/n-schilling/ha-app-nvme-monitor
expect 'device links to the app page' "$(dev nvme_monitor | jq -r .device.configuration_url)" \
    "homeassistant://hassio/addon/${HOSTNAME//-/_}/info"

echo '# Availability'
expect 'online after each round' "$(grep -c '^nvme_monitor/availability	online$' "${PUBLISHED}")" 2
expect 'offline on stop' "$(tail -1 "${PUBLISHED}")" 'nvme_monitor/availability	offline'
expect 'held connection with a retained last will' \
    "$(grep -c -- '--will-topic nvme_monitor/availability --will-payload offline --will-retain' "${PUBLISHED}.sub")" 1

echo '# Moving from single entity discovery'
printf '%s\t%s\n' homeassistant/sensor/nvme_monitor/temp_composite/config '{"unique_id":"nvme_monitor_temp_composite"}' \
    homeassistant/sensor/nvme_monitor_nvme1/wear/config '{"unique_id":"nvme_monitor_nvme1_percent_used"}' > "${RETAINED}"
run_monitor /tests/fixtures/smart-log.json
expect 'old topics migrated' "$(grep -c '{"migrate_discovery":true}' "${PUBLISHED}")" 2
expect 'migration before the device message' \
    "$(awk -F '\t' '$1 == "homeassistant/sensor/nvme_monitor/temp_composite/config" && /migrate_discovery/ { m = NR }
                     $1 == "homeassistant/device/nvme_monitor/config" && !f { f = NR } END { print (m && m < f) }' "${PUBLISHED}")" 1
expect 'old topics cleared' "$(last homeassistant/sensor/nvme_monitor/temp_composite/config)|$(last homeassistant/sensor/nvme_monitor_nvme1/wear/config)" '|'
: > "${RETAINED}"

echo '# A worn drive'
NVME1_LOG=/tests/fixtures/smart-log-worn.json run_monitor /tests/fixtures/smart-log.json
expect 'drive problem names every reason' "$(last nvme_monitor_nvme1/health_problem)|$(last nvme_monitor_nvme1/health_problem/attr)" \
    'ON|{"reasons":["3 media errors","spare at 8 %, threshold 10 %","rated endurance used up (104 %)"]}'

echo '# Throttling and critical warning'
run_monitor /tests/fixtures/smart-log-throttling.json
expect 'throttling active after the counters grew' "$(last nvme_monitor/throttle_active)" ON
expect 'critical warning bit set' "$(last nvme_monitor/critical_warning)" ON
expect 'drive problem with its reason' "$(last nvme_monitor/health_problem)|$(last nvme_monitor/health_problem/attr)" \
    'ON|{"reasons":["critical warning 2"]}'
expect 'warning logged' "$(grep -c 'Thermal throttling active' "${WORK}/log")" 1

if (( FAILED )); then
    echo '--- service log:'
    cat "${WORK}/log"
    exit 1
fi
echo 'All tests passed'
