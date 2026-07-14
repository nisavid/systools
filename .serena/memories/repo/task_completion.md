# Repository task completion

A task is ready when:

1. The owning subproject's applicable behavior and quality gates pass.
2. `git diff --check` passes.
3. Any changed shared Serena configuration passes `serena project health-check
   .`, `serena project index .`, and `serena memories check`.
4. Task-owned changes are committed with a Conventional Commit and the exact
   remote ref is verified after publication.
