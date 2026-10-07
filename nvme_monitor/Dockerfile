ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-debian:trixie
# hadolint ignore=DL3006
FROM ${BUILD_FROM}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        nvme-cli \
        mosquitto-clients \
        jq \
    && rm -rf /var/lib/apt/lists/*

COPY rootfs /

ARG BUILD_ARCH BUILD_DATE BUILD_DESCRIPTION BUILD_NAME BUILD_REF BUILD_REPOSITORY BUILD_VERSION
# For the origin of the MQTT discovery
ENV APP_VERSION=${BUILD_VERSION}
LABEL \
    io.hass.name="${BUILD_NAME}" \
    io.hass.description="${BUILD_DESCRIPTION}" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version=${BUILD_VERSION}
