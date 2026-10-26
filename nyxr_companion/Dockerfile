# Debian rather than the Supervisor's Alpine base (BUILD_FROM): libcdio's audio-CD libraries
# and python3-libdiscid / musicbrainzngs come packaged.
FROM debian:trixie-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        cd-paranoia \
        curl \
        dbus-daemon \
        libcdio-cdda2t64 \
        libcdio19t64 \
        pulseaudio-utils \
        python3 \
        python3-aiohttp \
        python3-dbus-next \
        python3-libdiscid \
        python3-musicbrainzngs \
    && rm -rf /var/lib/apt/lists/*

# nyxr from the Nyx Refrain release (a static build; only the binary is taken from the
# package, checked against the release's SHA256SUMS). 0.9.5 added --input-live and
# --keep-volume.
ARG NYXR_VERSION=0.9.5
RUN set -eu; \
    deb="nyx-refrain_${NYXR_VERSION}-1_$(dpkg --print-architecture).deb"; \
    url="https://github.com/pStrikeZ/Nyx-Refrain/releases/download/v${NYXR_VERSION}"; \
    cd /tmp; \
    curl -fsSLO "${url}/${deb}"; \
    curl -fsSL "${url}/SHA256SUMS" | grep " ${deb}\$" | sha256sum -c -; \
    dpkg-deb -x "${deb}" pkg; \
    install -m 755 pkg/usr/bin/nyxr /usr/local/bin/nyxr; \
    rm -rf "${deb}" pkg
COPY nyxr_companion.py /opt/nyxr-companion/nyxr_companion.py

CMD ["python3", "/opt/nyxr-companion/nyxr_companion.py"]
