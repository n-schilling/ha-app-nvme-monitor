#!/bin/bash
# Tests for the monitor service. Run inside the built image:
#   docker run --rm -v "$PWD/tests:/tests:ro" <image> bash /tests/test_monitor.sh
# nvme and mosquitto_pub/_sub are stubs answering from tests/fixtures;
# bashio's Supervisor lookups are replaced by fixed values.
set -u

readonly RUN=/etc/s6-overlay/s6-rc.d/monitor/run
WORK=$(mktemp -d)
export PUBLISHED="${WORK}/published" NVME_SYSFS="${WORK}/sysfs" NVME_STATUS_DIR="${WORK}/status" PATH="/tests/stubs:${PATH}" RETAINED="${WORK}/retained"
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

# Runs the service for two readings, or three with $2. Between the readings
# the SMART log switches to $1 and then $2, so each one sees a change.
run_monitor() {
    export SMART_LOG="${WORK}/smart-log.json" SECOND="$1" THIRD="${2:-}"
    cp /tests/fixtures/smart-log.json "${SMART_LOG}"
    : > "${PUBLISHED}"
    : > "${PUBLISHED}.sub"
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
            case "${pauses}" in
                1) cp "${SECOND}" "${SMART_LOG}" ;;
                2) [[ -n "${THIRD}" ]] || exit 0; cp "${THIRD}" "${SMART_LOG}" ;;
                *) exit 0 ;;
            esac
        }
        source '"${RUN}"'
    ' > "${WORK}/log" 2>&1
}

# A SMART log in $WORK: the base fixture changed by the jq filter $2
variant() { jq -c "$2" /tests/fixtures/smart-log.json > "${WORK}/$1.json"; echo "${WORK}/$1.json"; }

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

# The status file of a drive for the web page, through a jq filter
status() { jq -c "$2" "${NVME_STATUS_DIR}/$1.json"; }
dev() { last "homeassistant/device/$1/config"; }

echo '# Drive problem'
run_monitor /tests/fixtures/smart-log.json
expect 'no drive problem' "$(last nvme_monitor/health_problem)|$(last nvme_monitor/health_problem/attr)" 'OFF|{"reasons":[]}'
expect 'no other state published' \
    "$(awk -F '\t' '$1 ~ /^nvme_monitor(_nvme[0-9])?\// && $1 !~ /health_problem|availability/ && $2 != ""' "${PUBLISHED}" | wc -l | tr -d ' ')" 0

echo '# Status file for the web page'
expect 'whole SMART log' "$(status nvme0 '[.readable, .smart.temperature, .smart.unsafe_shutdowns]')" '[true,314,61]'
expect 'drive and device' "$(status nvme0 '[.drive, .name, .model, .firmware]')" \
    '["nvme0","Host NVMe","Patriot M.2 P300 256GB","H240220a"]'
expect 'thresholds from the controller' "$(status nvme0 .thresholds)" \
    '{"warning_temp_c":70,"critical_temp_c":85,"throttle_stage1_c":78,"throttle_stage2_c":83}'
expect 'no throttling, no reasons, no errors' "$(status nvme0 '[.throttling, .reasons, .errors, .heat_margin]')" '[null,[],[],10]'
expect 'time of the reading' "$(status nvme0 '.at > 1700000000')" true
expect 'no file left half written' "$(find "${NVME_STATUS_DIR}" -name '*.tmp' | wc -l | tr -d ' ')" 0

echo '# Second drive without extra temperature sensors'
expect 'its own status file' "$(status nvme1 '[.name, .model, .smart.temperature]')" '["Host NVMe 1","Basic NVMe 1TB",300]'
expect 'unset throttling stages are left out' "$(status nvme1 .thresholds)" '{"warning_temp_c":70,"critical_temp_c":85}'
expect 'its own device' \
    "$(dev nvme_monitor_nvme1 | jq -r '.components.health_problem.unique_id + " " + .device.name + " " + .device.model')" \
    'nvme_monitor_nvme1_health_problem Host NVMe 1 Basic NVMe 1TB'
expect 'unreadable drive skipped' "$(grep -c 'Skipping nvme2' "${WORK}/log")|$(find "${NVME_STATUS_DIR}" -name 'nvme2*' | wc -l | tr -d ' ')" '1|0'

echo '# Values moved to the web page'
expect 'old states removed' "$(grep -c '^nvme_monitor/temp_composite	$' "${PUBLISHED}")|$(grep -c '^nvme_monitor_nvme1/throttle_active/attr	$' "${PUBLISHED}")" '1|1'

echo '# Crash sensors moved to Crash Guard'
expect 'old crash entities removed' "$(grep -c '^homeassistant/sensor/ha_host/crashes_[a-z]*/config	$' "${PUBLISHED}")" 2
expect 'old crash states removed' "$(last ha_host/crashes_window)|$(last nvme_monitor/crashes_window)" '|'
expect 'no crash entity announced' "$(grep -c 'crashes.*/config	{' "${PUBLISHED}")" 0

echo '# Discovery'
expect 'one message per drive' "$(grep -c '^homeassistant/device/[a-z0-9_]*/config	{' "${PUBLISHED}")" 2
expect 'one entity per drive' "$(dev nvme_monitor | jq -c '.components | keys')|$(dev nvme_monitor_nvme1 | jq -c '.components | keys')" \
    '["health_problem"]|["health_problem"]'
expect 'a problem sensor with the reasons as attribute' \
    "$(dev nvme_monitor | jq -c '.components.health_problem | [.platform, .device_class, .json_attributes_topic]')" \
    '["binary_sensor","problem","nvme_monitor/health_problem/attr"]'
expect 'no single entity topics' "$(grep -c '^homeassistant/[a-z_]*/nvme_monitor[a-z0-9_]*/[a-z0-9_]*/config	{' "${PUBLISHED}")" 0
expect 'unique ID unchanged (entity IDs depend on it)' \
    "$(dev nvme_monitor | jq -r .components.health_problem.unique_id)" nvme_monitor_health_problem
expect 'NVMe model from sysfs, trimmed' \
    "$(dev nvme_monitor | jq -r '.device.model + " " + .device.sw_version')" 'Patriot M.2 P300 256GB H240220a'
expect 'sensor expires after three intervals' "$(dev nvme_monitor | jq -r .components.health_problem.expire_after)" 180
expect 'availability for the whole device' "$(dev nvme_monitor | jq -r .availability_topic)" nvme_monitor/availability
expect 'origin names the app and its support URL' \
    "$(dev nvme_monitor | jq -r '.origin.support_url')" https://github.com/n-schilling/ha-app-nvme-monitor
expect 'device links to the app page' "$(dev nvme_monitor | jq -r .device.configuration_url)" \
    "homeassistant://hassio/addon/${HOSTNAME//-/_}/info"

echo '# Entities of 2.x removed'
# The device message of 2.x with two of its 14 entities
printf '%s\t%s\n' homeassistant/device/nvme_monitor/config \
    '{"components":{"temp_composite":{"platform":"sensor","unique_id":"nvme_monitor_temp_composite"},"health_problem":{"platform":"binary_sensor"},"throttle_active":{"p":"binary_sensor"}}}' \
    > "${RETAINED}"
run_monitor /tests/fixtures/smart-log.json
expect 'gone entities announced with their platform only' \
    "$(grep '^homeassistant/device/nvme_monitor/config	' "${PUBLISHED}" | head -1 | cut -f2 | jq -c '.components | {temp_composite, throttle_active}')" \
    '{"temp_composite":{"platform":"sensor"},"throttle_active":{"platform":"binary_sensor"}}'
expect 'then left out' "$(dev nvme_monitor | jq -c '.components | keys')" '["health_problem"]'
: > "${RETAINED}"

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
expect 'the same reasons in the status file' "$(status nvme1 .reasons | jq length)" 3

echo '# Error log'
run_monitor "$(variant errors '.num_err_log_entries = 3')"
expect 'entries with an error only' "$(status nvme0 '[.errors[] | [.error_count, .status_field]]')" '[[2,8194],[1,16388]]'
expect 'error log entries alone are no drive problem' "$(last nvme_monitor/health_problem)" OFF

echo '# Throttling and critical warning'
run_monitor /tests/fixtures/smart-log-throttling.json
expect 'drive problem with both reasons' "$(last nvme_monitor/health_problem)|$(last nvme_monitor/health_problem/attr)" \
    'ON|{"reasons":["critical warning 2","thermal throttling, the drive reports a temperature warning"]}'
expect 'throttling in the status file' "$(status nvme0 .throttling)" \
    '{"kind":"heat","detail":"the drive reports a temperature warning"}'
expect 'warning logged with the reason' "$(grep -c 'Thermal throttling on nvme0: the drive reports a temperature warning' "${WORK}/log")" 1

# The fixtures throttle at 78 °C and warn at 70 °C, so 60 °C is the lowest
# temperature that still explains throttling
echo '# Throttling counters without heat'
run_monitor "$(variant counter1 '.thm_temp1_trans_count += 30 | .thm_temp1_total_time += 2')" \
    "$(variant counter2 '.thm_temp1_trans_count += 60 | .thm_temp1_total_time += 3')"
expect 'no drive problem at 41 °C' "$(grep '^nvme_monitor/health_problem	' "${PUBLISHED}" | cut -f2 | sort -u)" OFF
expect 'status file names the counters' "$(status nvme0 .throttling)" '{"kind":"counter","detail":"41"}'
expect 'no warning' "$(grep -c 'WARNING' "${WORK}/log")" 0
expect 'one line for the whole episode' \
    "$(grep -c 'Throttling counters of nvme0 grow at 41 °C, more than 10 °C below its lowest threshold of 70 °C: not reported as throttling' "${WORK}/log")" 1

echo '# Throttling explained by a temperature'
run_monitor "$(variant warm '.thm_temp1_trans_count += 30 | .temperature = 333')"
expect 'drive problem at 60 °C' "$(last nvme_monitor/health_problem)|$(last nvme_monitor/health_problem/attr)" \
    'ON|{"reasons":["thermal throttling, at 60 °C, threshold 70 °C"]}'
expect 'reason in the warning' "$(grep -c 'Thermal throttling on nvme0: at 60 °C, threshold 70 °C' "${WORK}/log")" 1
run_monitor "$(variant cool '.thm_temp1_trans_count += 30 | .temperature = 332')"
expect 'no drive problem at 59 °C' "$(last nvme_monitor/health_problem)" OFF
run_monitor "$(variant warning_time '.thm_temp1_trans_count += 30 | .warning_temp_time = 1')"
expect 'drive problem after time above the warning temperature' "$(last nvme_monitor/health_problem)" ON
expect 'that reason in the warning' "$(grep -c 'Thermal throttling on nvme0: above its warning temperature' "${WORK}/log")" 1

echo '# End of throttling'
hot=$(variant hot '.thm_temp1_trans_count += 30 | .temperature = 345')
run_monitor "${hot}" "${hot}"
expect 'off once the counters stand still' "$(last nvme_monitor/health_problem)|$(status nvme0 .throttling)" 'OFF|null'
expect 'one warning, one end' "$(grep -c 'Thermal throttling on nvme0:' "${WORK}/log")|$(grep -c 'Thermal throttling on nvme0 ended' "${WORK}/log")" '1|1'

echo '# Unreadable drive'
: > "${WORK}/empty.json"
run_monitor "${WORK}/empty.json"
expect 'status file keeps the last log' "$(status nvme0 '[.readable, .smart.temperature]')" '[false,314]'
expect 'one warning' "$(grep -c 'Reading nvme0 failed, retrying every interval' "${WORK}/log")" 1

echo '# Broker away'
rm -f "${PUBLISHED}.calls"
PUB_FAIL=999 run_monitor /tests/fixtures/smart-log.json
expect 'no state published' "$(awk -F '\t' '$1 ~ /^nvme_monitor/ && $2 != ""' "${PUBLISHED}" | wc -l | tr -d ' ')" 0
expect 'one warning for the whole outage, with the reason' \
    "$(grep -c 'MQTT broker not reachable (Error: Connection refused), retrying every interval' "${WORK}/log")" 1
expect 'not mistaken for an unreadable drive' "$(grep -c 'Reading nvme[0-9] failed' "${WORK}/log")" 0
expect 'no raw errors from the rounds' "$(grep -c '^Error: ' "${WORK}/log")" 0
expect 'a round stops at its first failed message (2 rounds + offline on stop)' "$(cat "${PUBLISHED}.calls")" 3

echo '# Broker back'
rm -f "${PUBLISHED}.calls"
PUB_FAIL=1 run_monitor /tests/fixtures/smart-log.json
expect 'warning while away' "$(grep -c 'MQTT broker not reachable' "${WORK}/log")" 1
expect 'back in the log' "$(grep -c 'MQTT broker answers again' "${WORK}/log")" 1
expect 'states published again' "$(last nvme_monitor/health_problem)|$(grep -c '^nvme_monitor/availability	online$' "${PUBLISHED}")" 'OFF|1'
rm -f "${PUBLISHED}.calls"

if (( FAILED )); then
    echo '--- service log:'
    cat "${WORK}/log"
    exit 1
fi
echo 'All tests passed'
