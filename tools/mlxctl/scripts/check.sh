#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
uv sync --locked
uv run --locked pyrefly check
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest
uv run --locked uv build --no-build-isolation --clear
git diff --check
