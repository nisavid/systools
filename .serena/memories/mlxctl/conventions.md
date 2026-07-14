# mlxctl conventions

- Use the ubiquitous language in `src/mlxctl/CONTEXT.md`: Server Definition, Server Type, Model Alias, Model Reference, Client Endpoint, Upstream Endpoint, Supervisor, Probe, Request Metric Event, Process Sample, and Metric Query.
- Keep package code under `src/mlxctl` and mirror behavior in focused `tests/test_*.py` modules.
- Prefer immutable dataclasses for observations and snapshots.
- Keep transport decoding, validation, and error responses explicit; do not expose machine-local paths or tracebacks through the control protocol.
- Keep I/O boundaries injectable so tests can use temporary directories, socket doubles, fake clocks, and PTYs.
- User-facing commands should return bounded, actionable errors and preserve responsive control behavior.
- Machine-specific server definitions, model references, paths, and launchd values belong in the dotfiles deployment layer.
