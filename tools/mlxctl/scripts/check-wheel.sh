#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
check_directory=$(mktemp -d)
trap 'rm -rf "$check_directory"' EXIT HUP INT TERM

uv export --locked --no-dev --no-emit-project --output-file "$check_directory/requirements.txt" > /dev/null
uv venv "$check_directory/environment"
uv pip sync --python "$check_directory/environment/bin/python" "$check_directory/requirements.txt"
set -- dist/mlxctl-*.whl
if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
  echo "Build exactly one mlxctl wheel in dist/ before checking it." >&2
  exit 1
fi
uv pip install --python "$check_directory/environment/bin/python" --no-deps "$1"
"$check_directory/environment/bin/mlxctl" --help
"$check_directory/environment/bin/mlxd" --help
