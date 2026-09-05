# remote_ci_monitor — Linux build-machine server image. macOS build machines use launchd instead.
#
#   docker build -t rcm .
#   docker run -d --name rcm -p 127.0.0.1:8787:8787 \
#     -v rcm-data:/data -v "$PWD/server.toml:/config/server.toml:ro" rcm
#   docker exec rcm rcm token add laptop --data-dir /data
#
# Inside a container `ps`/`/proc` see only the container, so host pressure is less accurate than a
# native service; GPU numbers need an NVIDIA base image and `--gpus all`. Publish the port on
# 127.0.0.1 or a Tailscale IP only — the server does no TLS and reads are open by default.
FROM python:3.12-slim

# git for git_ref presets (https + ssh), procps for `ps`, bash for the example presets.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates openssh-client procps bash \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 --shell /bin/bash rcm

WORKDIR /src
COPY pyproject.toml README.md LICENSE CHANGELOG.md ./
COPY src ./src
RUN pip install --no-cache-dir . && rm -rf /src

# /data must exist and belong to rcm before it is declared a volume: a fresh named volume copies
# the image directory's ownership, and a root-owned /data would make the non-root server fail to
# create its SQLite database on first start. /config is read-only (bind-mounted server.toml).
RUN mkdir -p /data /config && chown rcm:rcm /data

USER rcm
WORKDIR /home/rcm
VOLUME ["/data", "/config"]
EXPOSE 8787
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["rcm"]
CMD ["serve", "--config", "/config/server.toml", "--data-dir", "/data", "--bind", "0.0.0.0"]
