# mlxctl commands

Run from `tools/mlxctl/`.

## Development and verification

- `uv run python -m unittest discover -s tests` — run the full test suite with project dependencies.
- `uvx ruff check .` — lint the tool's Python files.
- `uvx ruff format --check .` — verify formatting.
- `uv build` — build the source distribution and wheel.

## Local package use

- `uv tool install --force .` — install or refresh `mlxctl` and `mlxd` from this tool root.
- `mlxctl status` — inspect managed server state.
- `mlxctl dashboard` — open the terminal dashboard.
- `mlxctl start <server>` / `mlxctl stop <server>` — manage a named Server Definition.

The dotfiles install hook owns the normal machine installation and LaunchAgent registration.
