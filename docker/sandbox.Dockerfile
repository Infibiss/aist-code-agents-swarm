# Base image for task sandboxes: one per Python version; task environments live in named volumes.
# Docker Hub and deb.debian.org are slow or unreachable from some networks, so both sources are overridable.
ARG PY=3.11
ARG REGISTRY=mirror.gcr.io/library
FROM ${REGISTRY}/python:${PY}-slim
ARG APT_MIRROR=mirror.yandex.ru
RUN if [ -n "$APT_MIRROR" ]; then \
      sed -i "s|deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/*.sources /etc/apt/sources.list 2>/dev/null; \
    fi; \
    apt-get update \
 && apt-get install -y --no-install-recommends git patch binutils \
 && rm -rf /var/lib/apt/lists/*
RUN git config --system user.email sandbox@local && git config --system user.name sandbox \
 && git config --system --add safe.directory '*'
WORKDIR /testbed
