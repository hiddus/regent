FROM python:3.12.10-slim

# Agent command sandbox (CD-6.1): no ENTRYPOINT — argv supplies ``sh -lc``.
# Distinct from build sandbox (capabilities/bootstrap/sandbox) which uses python ENTRYPOINT.
ENV HOME=/tmp \
    PIP_CACHE_DIR=/tmp/pip \
    XDG_CACHE_HOME=/tmp/cache \
    PYTHONDONTWRITEBYTECODE=1

RUN pip install --no-cache-dir pytest==8.4.1 \
    && mkdir -p /tmp/pip /tmp/cache \
    && chmod 1777 /tmp

# Non-root default; runtime ``--user`` may override to match worker uid (CD-6.2).
USER 65532:65532
WORKDIR /workspace
# Intentionally no ENTRYPOINT / CMD — DockerSandboxDriver passes --entrypoint sh
