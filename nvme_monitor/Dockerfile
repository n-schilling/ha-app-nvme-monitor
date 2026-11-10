# The Supervisor builds the image on the host; Dependabot proposes new tags
FROM ghcr.io/home-assistant/base-debian:trixie-2026.11.0

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        nvme-cli \
        mosquitto-clients \
        jq \
        python3 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin nvmeweb

COPY rootfs /

ARG BUILD_ARCH BUILD_DATE BUILD_DESCRIPTION BUILD_NAME BUILD_REF BUILD_REPOSITORY BUILD_VERSION
# For the origin of the MQTT discovery and the version line of the web page
ENV APP_VERSION=${BUILD_VERSION}
# s6-overlay: warnings and errors only, no per-service start/stop info lines
ENV S6_VERBOSITY=1
LABEL \
    io.hass.name="${BUILD_NAME}" \
    io.hass.description="${BUILD_DESCRIPTION}" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version=${BUILD_VERSION}
