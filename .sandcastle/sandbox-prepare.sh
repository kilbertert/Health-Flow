#!/usr/bin/env bash
#
# Prepare a HealthFlow sandbox for an AFK run.
#
# A run's workspace starts empty — `frontend/node_modules/`, `dist/` and `.venv/`
# are all gitignored. Without this hook the agent installs them itself, and an
# agent that sees "not installed" cannot tell *this checkout was never set up*
# from *this sandbox lacks the prerequisite*, so it downloads one. Measured: ten
# minutes spent fetching a 150 MB browser build for a browser the image already
# has (`chromium.launch()` succeeds; `playwright install chromium` is a no-op).
#
# Idempotent: sandcastle runs this once per iteration, not once per run.
set -euo pipefail

# `--extra dev`, NOT `--dev`: this repo puts pytest/ruff in
# `[project.optional-dependencies] dev`, so `--dev` installs nothing and exits 0.
# CI carries the same note for the same reason (ci.yml).
uv sync --locked --extra dev

cd frontend
npm ci
# `dist/` is a build artifact and the server refuses to start without it. The
# verification skill's single-spec path does not skip this.
npm run build

