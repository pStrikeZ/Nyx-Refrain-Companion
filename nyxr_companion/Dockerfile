# shairport-sync's own image rather than the Supervisor's base (BUILD_FROM): it brings
# shairport-sync with AirPlay 2, nqptp and avahi, which distributions package either without
# AirPlay 2 or not at all. It is Alpine; the rest comes from Alpine's packages.
# Pinned by digest: the tag is moved for rebuilds.
FROM mikebrady/shairport-sync:5.6.2@sha256:b6192daba080e2511716490fa104a0bf96ebbb6dbf49d42a39243d9d6c5f97f9

RUN apk add --no-cache \
        ca-certificates \
        libcdio \
        libcdio-paranoia \
        libdiscid \
        pulseaudio-utils \
        py3-aiohttp \
        py3-dbus-next \
        py3-musicbrainzngs \
        python3

# nyxr from the Nyx Refrain release (a static build; only the binary is taken from the
# package, checked against the release's SHA256SUMS). 0.9.5 added --input-live and
# --keep-volume.
ARG NYXR_VERSION=0.9.5
RUN set -eu; \
    apk add --no-cache --virtual .fetch curl tar zstd; \
    pkg="nyx-refrain-${NYXR_VERSION}-1-$(apk --print-arch).pkg.tar.zst"; \
    url="https://github.com/pStrikeZ/Nyx-Refrain/releases/download/v${NYXR_VERSION}"; \
    cd /tmp; \
    curl -fsSLO "${url}/${pkg}"; \
    curl -fsSL "${url}/SHA256SUMS" | grep " ${pkg}\$" | sha256sum -c -; \
    tar --zstd -xf "${pkg}" usr/bin/nyxr; \
    install -m 755 usr/bin/nyxr /usr/local/bin/nyxr; \
    rm -rf "${pkg}" usr; \
    apk del .fetch
# Only our AirPlay receiver is advertised (the image also has ssh / sftp services).
RUN rm -f /etc/avahi/services/*
COPY nyxr_companion.py /opt/nyxr-companion/nyxr_companion.py

# The daemon starts avahi, nqptp and shairport-sync itself (the image's run.sh would start
# a D-Bus system bus where the host's is mounted).
ENTRYPOINT []
CMD ["python3", "/opt/nyxr-companion/nyxr_companion.py"]
