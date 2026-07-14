# mlxctl

`mlxctl` is the Python package for managing named local MLX inference servers.

## Source map

- `src/mlxctl/` contains the package, CLI, daemon, control protocol,
  supervisor, metrics proxy, adapters, and terminal dashboard.
- `tests/` contains behavior and transport contract tests.
- `src/mlxctl/CONTEXT.md` is the domain glossary; use its terms in code and
  user-facing copy.
- `docs/deployment-contract.md` is the versioned boundary with the personal
  dotfiles deployment layer.

## Invariants

- A Server Definition names a Server Type, Model Alias, and stable Client
  Endpoint.
- The Supervisor owns lifecycle; `mlxctl` communicates with `mlxd` over the
  versioned Unix-socket control protocol.
- Keep machine-local deployment values in the dotfiles repository, not in this
  package.

See `mem:mlxctl/tech_stack`, `mem:mlxctl/conventions`,
`mem:mlxctl/suggested_commands`, and `mem:mlxctl/task_completion`.
