# mlxctl Deployment Contract v1

The interface between `mlxctl` (this repo) and `nisavid/dotfiles` (the
personal deployment layer). The dotfiles agent implements this contract
with personal values: the LaunchAgent plist, chezmoi config data, and
install hook.

## Entry Points

| Name     | Purpose                          | Installed by        |
|----------|----------------------------------|---------------------|
| `mlxctl` | CLI/TUI — status, start, stop, models, metrics, dashboard | `pyproject.toml` `console_scripts` |
| `mlxd`   | Supervisor daemon — launchd-managed process that manages MLX server lifecycle | `pyproject.toml` `console_scripts` |

Both are installed via `uv tool install` or `pip install` as console
script entry points. The plist's `ProgramArguments` invokes `mlxd`.

## Plist Label

```
io.nisavid.mlxd
```

Reverse-DNS of the personal domain `nisavid.io`. The LaunchAgent plist
file is `~/Library/LaunchAgents/io.nisavid.mlxd.plist`.

## Directory Layout

| Purpose | Default path                      | Env override            |
|---------|-----------------------------------|-------------------------|
| Config  | `~/.config/mlxd/`                 | `MLXD_CONFIG_DIR`       |
| State   | `~/.local/state/mlxd/`            | `MLXD_STATE_DIR`        |
| Logs    | `~/Library/Logs/mlxd/`            | `MLXD_LOG_DIR`          |

Follows XDG Base Directory Specification for config and state; macOS
convention for logs.

### Config directory (`~/.config/mlxd/`)

Server definitions, model registry, default inference params, metrics
retention settings. File format decided separately (see wayfinder ticket
#7).

### State directory (`~/.local/state/mlxd/`)

Runtime state: supervisor PID file, control socket, metrics database
(SQLite, see wayfinder ticket #6).

### Log directory (`~/Library/Logs/mlxd/`)

`mlxd.log` (supervisor stdout/stderr), per-server logs
(`mlx_lm-<model>.log`, `optiq-<model>.log`).

## Environment Variables

The plist sets these in `EnvironmentVariables`:

| Variable              | Purpose                                          |
|-----------------------|--------------------------------------------------|
| `PATH`                | Must include `~/.local/bin/` where `uv tool` installs `mlx_lm`, `optiq`, and other MLX tool entry points |
| `MLXD_CONFIG_DIR`     | Override config directory (defaults to `~/.config/mlxd/`) |
| `MLXD_STATE_DIR`      | Override state directory (defaults to `~/.local/state/mlxd/`) |
| `MLXD_LOG_DIR`        | Override log directory (defaults to `~/Library/Logs/mlxd/`) |

## LaunchAgent Behavior

- `RunAtLoad=false` — disabled by default; the service does not start on
  login.
- `KeepAlive=false` — launchd does not auto-restart the supervisor.
- The user starts inference explicitly via `mlxctl start <server>`.
- The plist is installed but unloaded. The user loads it via
  `launchctl load` or `mlxctl service install` (future).

## Follow-up (Not in v1)

These graduate as separate contract revisions once the relevant wayfinder
tickets resolve:

- **Control socket** — path and protocol for CLI↔daemon IPC
  (Unix socket at `~/.local/state/mlxd/mlxd.sock` is the expected
  default; graduates after supervisor-topology decision, ticket #4).
- **Server port allocation** — how `mlxctl` assigns ports to individual
  MLX servers (static config vs. dynamic allocation).
- **Log level** — `MLXD_LOG_LEVEL` env var.
- **Full env-var set** — any additional variables the supervisor needs.
