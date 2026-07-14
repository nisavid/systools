# Systools

Systools is a repository for machine-local utilities. The Git repository root is
the shared Serena project boundary.

## Source map

- `src/`, `tests/`, and `docs/` contain subproject-owned implementation,
  verification, and documentation.
- `CONTEXT-MAP.md` routes paths and behavior to the applicable product context.
- `.github/` and `.hooks/` contain repository-wide commit-policy automation.
- `.serena/memories/repo/` contains repository-wide guidance.
- Each subproject keeps settled domain and implementation guidance in its own
  `.serena/memories/<subproject>/` namespace.

## Project invariants

- The repository root is the project boundary; do not model sibling Git worktrees as workspace folders.
- Route work by path and load the owning subproject's memories before changing
  its behavior.
- Read the path-relevant context named by `CONTEXT-MAP.md` before applying
  product vocabulary.
- Treat a future nested Git repository or submodule as a separate Serena
  project.
- Add a new subproject memory namespace only after its decisions are settled.

See `mem:repo/conventions`, `mem:repo/suggested_commands`, and
`mem:repo/task_completion`. For MLX service-manager work, start with
`mem:mlxctl/core`.
