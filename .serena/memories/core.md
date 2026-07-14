# Systools

Systools is a repository for machine-local utilities. Its current product is the `mlxctl` Python package, which manages named local MLX inference servers.

## Source map

- `src/mlxctl/` contains the package, CLI, daemon, control protocol, supervisor, metrics proxy, adapters, and terminal dashboard.
- `tests/` contains the behavior and transport contract tests.
- `CONTEXT.md` is the domain glossary; use its terms in code and user-facing copy.
- `docs/deployment-contract.md` is the versioned boundary with the personal dotfiles deployment layer.
- `.github/` and `.hooks/` contain repository-wide commit-policy automation.

## Project invariants

- The repository root is the project boundary; do not model sibling Git worktrees as workspace folders.
- A Server Definition names a Server Type, Model Alias, and stable Client Endpoint.
- The Supervisor owns lifecycle; `mlxctl` communicates with `mlxd` over the versioned Unix-socket control protocol.
- Keep machine-local deployment values in the dotfiles repository, not in this package.

See `mem:tech_stack`, `mem:conventions`, `mem:suggested_commands`, and `mem:task_completion`.
