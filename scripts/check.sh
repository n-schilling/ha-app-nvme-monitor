#!/bin/bash
# Runs locally what the CI workflow runs, so a push only confirms what already
# passed here: the changelog check, shellcheck, ruff, the
# image build with its tests (monitor, image, web page) and the test against
# a real MQTT broker. Keep the steps in step with .github/workflows/ci.yaml.
#
# Usage: scripts/check.sh [--no-docker]
#
# The shell linter comes from PATH, else from Docker; ruff from uvx, else PATH. The
# app linter (frenck/action-addon-linter) runs in CI only.
# The image is built for this machine's architecture only; CI builds amd64
# and aarch64. --no-docker skips everything that needs Docker.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

readonly RUFF=ruff==0.17.3
readonly BROKER=eclipse-mosquitto:2.1.2-alpine
readonly IMAGE=nvme-monitor:check

failed=()
skipped=()

run() { # run <name> <command...>
    local name=$1
    shift
    echo "==> ${name}"
    if "$@"; then
        echo "    ok: ${name}"
    else
        echo "    FAILED: ${name}"
        failed+=("${name}")
    fi
    echo
}

docker=true
for arg in "$@"; do
    case "${arg}" in
        --no-docker) docker=false ;;
        *) echo "unknown option ${arg}" >&2; exit 2 ;;
    esac
done
if ${docker} && ! docker info > /dev/null 2>&1; then
    docker=false
    skipped+=("everything with Docker (Docker is not running)")
elif ! ${docker}; then
    skipped+=("everything with Docker (--no-docker)")
fi

version=$(sed -n 's/^version: *"\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' nvme_monitor/config.yaml)

no_pyc() { ! git ls-files | grep -E '(^|/)__pycache__/|\.pyc$'; }
shell_scripts() {
    local files=(nvme_monitor/rootfs/etc/s6-overlay/s6-rc.d/*/run nvme_monitor/rootfs/usr/local/lib/mqtt-device.sh
                 tests/test_*.sh tests/stubs/nvme tests/stubs/mosquitto_* scripts/*.sh)
    local args=(--shell=bash --severity=warning --external-sources)
    if command -v shellcheck > /dev/null; then
        shellcheck "${args[@]}" "${files[@]}"
    elif ${docker}; then
        docker run --rm -v "${PWD}:/mnt:ro" -w /mnt koalaman/shellcheck:stable "${args[@]}" "${files[@]}"
    else
        echo "no shellcheck and no Docker" >&2
        return 1
    fi
}
ruff_check() {
    if command -v uvx > /dev/null; then
        uvx "${RUFF}" check nvme_monitor/rootfs/usr/local/lib tests scripts
    else
        ruff check nvme_monitor/rootfs/usr/local/lib tests scripts
    fi
}
in_image() { # in_image <entrypoint> <args...>
    local entry=$1
    shift
    docker run --rm --network none -v "${PWD}/tests:/tests:ro" --entrypoint "${entry}" "${IMAGE}" "$@"
}
broker_test() {
    local net=nvme-check-$$ rc=0
    docker network create "${net}" > /dev/null
    docker run -d --name "${net}-broker" --network "${net}" --network-alias broker \
        -v "${PWD}/tests/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro" "${BROKER}" > /dev/null
    docker run --rm --network "${net}" --add-host supervisor:127.0.0.1 -v "${PWD}/tests:/tests:ro" \
        --entrypoint bash "${IMAGE}" /tests/test_broker.sh || rc=$?
    docker rm -f "${net}-broker" > /dev/null
    docker network rm "${net}" > /dev/null
    return "${rc}"
}

run "Changelog has the version ${version}" bash -c \
    '.github/scripts/changelog-section.sh nvme_monitor/CHANGELOG.md "$1" > /dev/null' _ "${version}"
run "No compiled Python in the repository" no_pyc
run "Shell scripts" shell_scripts
run "Python" ruff_check

if ${docker}; then
    if run "Build" docker build -q --build-arg "BUILD_VERSION=${version}" -t "${IMAGE}" nvme_monitor; then
        run "Test the monitor" in_image bash /tests/test_monitor.sh
        run "Test the image" in_image bash /tests/test_image.sh
        run "Test the web page" in_image python3 -m unittest discover -s /tests -p 'test_*.py'
        run "Against a real MQTT broker" broker_test
    fi
fi
skipped+=("App configuration (frenck/action-addon-linter runs in CI only)")

echo "===================="
for name in ${skipped[@]+"${skipped[@]}"}; do
    echo "skipped: ${name}"
done
if ((${#failed[@]})); then
    echo "FAILED: ${failed[*]}"
    exit 1
fi
echo "All checks passed."
