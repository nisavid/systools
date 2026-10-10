#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
uv sync --locked
uv run --locked pyrefly check
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest
build_python=$(uv run --locked python -c 'import sys; print(sys.executable)')
uv run --locked uv build --python "$build_python" --no-build-isolation --clear
git diff --check
