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

Runtime state: supervisor lifecycle state, the `mlxd.sock` control socket,
and the `metrics.db` SQLite database. The directory is mode `0700`; the
socket, database, and lifecycle state files are mode `0600`.

## Control Protocol v1

`mlxctl` connects to `mlxd.sock` using newline-delimited JSON over a Unix
socket. Each connection carries one versioned request and one response.
The protocol supports server start, stop, status, advertised-model, and
metric-summary commands. `mlxd` rejects malformed, unsupported, unknown,
and oversized requests without exposing machine-local paths or tracebacks.

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

- `RunAtLoad=false` — the registered service does not start on login.
- `KeepAlive=false` — launchd does not auto-restart the supervisor.
- The deployment layer registers the plist in the user's launchd domain.
- When the control socket is absent, `mlxctl` runs `launchctl kickstart`
  for `io.nisavid.mlxd` and waits boundedly for the socket.
- The user starts inference explicitly via `mlxctl start <server>`; the
  daemon exits after its idle grace once no managed servers remain active.

## Follow-up (Not in v1)

These graduate as separate contract revisions once the relevant wayfinder
tickets resolve:

- **Server port allocation** — how `mlxctl` assigns ports to individual
  MLX servers (static config vs. dynamic allocation).
- **Log level** — `MLXD_LOG_LEVEL` env var.
- **Full env-var set** — any additional variables the supervisor needs.
