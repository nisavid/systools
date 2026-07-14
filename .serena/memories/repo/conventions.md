# Repository conventions

- Treat the Git repository root as the shared project boundary.
- Keep each independently shipped product beneath `tools/<tool>/`, with its
  package metadata, implementation, tests, context, and product documentation.
- Route work to the owning subproject by path and keep its durable guidance in
  a namespaced memory directory.
- Keep repository-wide policy under `repo/*`; do not promote unsettled
  subproject decisions into shared guidance.
- Treat nested Git repositories and submodules as separate Serena projects.
- Never add sibling worktrees as Serena workspace folders.
- Use Conventional Commits.
