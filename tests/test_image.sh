#!/bin/bash
# Tests of the image itself: the web page's own user and how web/run starts
# the page. Run inside the built image:
#   docker run --rm -v "$PWD/tests:/tests:ro" <image> bash /tests/test_image.sh
set -u

readonly RUN=/etc/s6-overlay/s6-rc.d/web/run
WORK=$(mktemp -d)
FAILED=0

expect() {
    local name=$1 got=$2 want=$3
    if [[ "${got}" == "${want}" ]]; then
        echo "ok    ${name}"
    else
        echo "FAIL  ${name}: got '${got}', want '${want}'"
        FAILED=1
    fi
}

echo '# The page user'
expect 'a system user without a shell' "$(getent passwd nvmeweb | cut -d: -f7)" /usr/sbin/nologin
expect 'no root and no capabilities after the switch' \
    "$(s6-setuidgid nvmeweb sh -c 'id -u; grep -E "^Cap(Eff|Prm|Amb)" /proc/self/status' | tr -s '\t\n' ' ')" \
    "$(id -u nvmeweb) CapPrm: 0000000000000000 CapEff: 0000000000000000 CapAmb: 0000000000000000 "
expect 'no compiled Python in the image' "$(find /usr/local/lib -name '*.pyc' -o -name __pycache__ | wc -l)" 0

echo '# web/run'
# s6-setuidgid is a stub that records how the page would start
mkdir -p "${WORK}/bin" /data
cat > "${WORK}/bin/s6-setuidgid" <<'STUB'
#!/bin/bash
printf '%s|%s|%s\n' "$*" "${NVME_INTERVAL}" "$(stat -c '%U %a' /data/web /data/web/history.json | tr '\n' ' ')" > "${STARTED}"
STUB
chmod +x "${WORK}/bin/s6-setuidgid"
# The history of 3.0.0 in /data moves to the page's own folder
printf '{"drives":{}}' > /data/history.json
STARTED="${WORK}/started" PATH="${WORK}/bin:${PATH}" bash -c '
    source /usr/lib/bashio/bashio.sh
    bashio::config() { [[ "$1" == interval ]] && echo 90; }
    source "$0"
' "${RUN}" > "${WORK}/log" 2>&1
expect 'starts the page as its user with the interval' "$(cut -d'|' -f1-2 "${WORK}/started" 2>/dev/null)" \
    'nvmeweb python3 /usr/local/lib/nvme_web.py|90'
expect 'history moved and owned by that user' "$(cut -d'|' -f3 "${WORK}/started" 2>/dev/null)|$(ls /data)" \
    'nvmeweb 700 nvmeweb 644 |web'

if (( FAILED )); then
    echo '--- log:'
    cat "${WORK}/log"
    exit 1
fi
echo 'All tests passed'
