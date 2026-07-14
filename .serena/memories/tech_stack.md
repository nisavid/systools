# Technology stack

- Python 3.11 or newer.
- `hatchling` builds the `mlxctl` distribution from `src/mlxctl`.
- `uv` manages local execution, builds, and tool installation.
- Runtime code is primarily Python standard library; `psutil` is installed on Darwin arm64 for process metrics.
- Console entry points are `mlxctl = mlxctl.cli:main` and `mlxd = mlxctl.daemon:main`.
- Tests use the standard-library `unittest` framework, including Unix-socket and PTY coverage.
- Ruff provides lint and formatting checks.
- Cocogitto validates Conventional Commit messages in hooks and GitHub Actions.
- The live deployment target is macOS launchd; personal values and LaunchAgent templates live in `nisavid/dotfiles`.
