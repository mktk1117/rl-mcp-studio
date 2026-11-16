# A fresh machine, on purpose.
#
# `build` turns this checkout into a wheel, the same artifact a user gets; the
# setup.py hook downloads the pinned browser libraries into it. `fresh` is an
# empty Python with nothing of this tree in it: it installs that wheel and
# nothing else, so if the wheel lacks a file, `rlmcp-studio --check` in the
# container is where it shows. `train` adds rl-mcp's mjlab extra.
#
# Works under podman and docker alike. `scripts/fresh-install.sh` drives it;
# by hand:
#
#   podman build -t rlmcp-studio:fresh .
#   podman run --rm rlmcp-studio:fresh --check
#   podman run --rm -p 8730:8730 rlmcp-studio:fresh --host 0.0.0.0
#
# With a GPU (needs the NVIDIA container toolkit and its CDI spec):
#
#   podman run --rm --device nvidia.com/gpu=all rlmcp-studio:fresh --check

ARG PYTHON=3.12
ARG UV_IMAGE=ghcr.io/astral-sh/uv:python${PYTHON}-bookworm-slim

# ── build: checkout → wheel ─────────────────────────────────────────────────
FROM ${UV_IMAGE} AS build
WORKDIR /src
# Only what `uv build` reads; .dockerignore keeps everything else out of the
# context, so a stray local file cannot make the container pass for the wrong
# reason.
COPY pyproject.toml setup.py README.md LICENSE ./
COPY rlmcp_studio ./rlmcp_studio
RUN uv build --wheel --out-dir /dist

# ── fresh: an empty machine that installs the wheel ────────────────────────
FROM ${UV_IMAGE} AS fresh

# rl-mcp from PyPI, as a user gets it. Override to test an unreleased branch:
#   --build-arg 'RLMCP_SPEC=rl-mcp[server] @ git+https://github.com/mktk1117/rl-mcp@branch'
ARG RLMCP_SPEC="rl-mcp[server]"

# git for a git-sourced rl-mcp and for the code stamp taken at launch; curl for
# the smoke test's probes.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# A user, not root: the studio writes settings and state under $HOME, and a
# fresh install runs as whoever installed it.
RUN useradd --create-home --shell /bin/bash studio
USER studio
WORKDIR /home/studio
ENV PATH="/home/studio/.local/bin:${PATH}" \
    UV_TOOL_BIN_DIR=/home/studio/.local/bin

COPY --from=build /dist/*.whl /tmp/dist/
# The README's install line: a tool install of the wheel with rl-mcp's server
# extra. `--with` puts rl-mcp's scripts in the tool's venv, not on PATH.
RUN uv tool install --with "${RLMCP_SPEC}" /tmp/dist/*.whl \
 && rlmcp-studio --help >/dev/null

# Where a run's records go inside the container. Mount a host directory here
# to keep them; the smoke test does not.
ENV RLMCP_RECORDS=/home/studio/records
RUN mkdir -p /home/studio/records
EXPOSE 8730

ENTRYPOINT ["rlmcp-studio"]
CMD ["--host", "0.0.0.0", "--port", "8730"]

# ── train: the same fresh machine, able to train ───────────────────────────
# One install line, and mjlab's built-in tasks appear in the switcher and
# train from the launch button. rl-mcp's `mjlab` extra brings torch,
# mujoco-warp and rsl-rl (several GB, so CI does not build this target):
#
#   podman build --target train -t rlmcp-studio:train .
#   podman run --rm --device nvidia.com/gpu=all rlmcp-studio:train --check
#
# The studio lists tasks through its *own* interpreter when there is no task
# repository beside the records (sources/taskenv.py), so mjlab in this venv is
# all it takes for mjlab's built-in tasks to be listed.
FROM fresh AS train
# Importing a mjlab task loads MuJoCo's headless renderer, which needs
# libEGL.so.1; a slim image lacks it and fails before any task is listed.
# glvnd dispatches to the NVIDIA driver's EGL when the GPU is passed through
# and to Mesa's when it is not.
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libegl1 libgles2 libgl1 libglvnd0 libegl-mesa0 libgl1-mesa-dri libopengl0 \
 && rm -rf /var/lib/apt/lists/*
USER studio
# Headless rendering, set explicitly rather than guessed from the absence of a
# display.
ENV MUJOCO_GL=egl PYOPENGL_PLATFORM=egl
ARG RLMCP_TRAIN_SPEC="rl-mcp[server,mjlab]"
# A cache mount for uv's downloads, so an interrupted build resumes instead of
# downloading torch again. The mount is never part of the image.
RUN --mount=type=cache,target=/home/studio/.cache/uv,uid=1000,gid=1000 \
    uv tool install --force --with "${RLMCP_TRAIN_SPEC}" /tmp/dist/*.whl \
 && rlmcp-studio --help >/dev/null
# The cache mount creates its parent directories as root, which leaves
# /home/studio root-owned and makes the first new directory under it (such as
# `rlmcp hostd`'s sessions root) fail with EACCES. Hand the home back, and
# check it at build time.
USER root
RUN chown studio:studio /home/studio
USER studio
RUN touch /home/studio/.writable && rm /home/studio/.writable
# `uv tool install` exposes only the studio's own scripts; link rl-mcp's from
# the same venv so `rlmcp` and `rlmcp-train` work at a shell.
RUN for s in rlmcp rlmcp-server rlmcp-train; do \
      ln -sf "$(uv tool dir)/rlmcp-studio/bin/$s" "/home/studio/.local/bin/$s"; \
    done && rlmcp --help >/dev/null
# Where rl-mcp writes runs when a launch does not say (`--log-root` default).
WORKDIR /home/studio/work
