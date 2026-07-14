# mlxctl technology stack

- Python 3.11 or newer.
- `hatchling` builds the `mlxctl` distribution from `tools/mlxctl/src/mlxctl`.
- `uv` manages local execution, builds, and tool installation.
- Runtime code is primarily Python standard library; `psutil` is installed on Darwin arm64 for process metrics.
- Console entry points are `mlxctl = mlxctl.cli:main` and `mlxd = mlxctl.daemon:main`.
- Tests use the standard-library `unittest` framework, including Unix-socket and PTY coverage.
- Ruff provides lint and formatting checks.
- The live deployment target is macOS launchd; personal values and LaunchAgent templates live in `nisavid/dotfiles`.
