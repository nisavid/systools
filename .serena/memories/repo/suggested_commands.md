# Repository commands

Run from the repository root.

- `git status --short --branch` — inspect the current checkout and dirt.
- `git diff --check` — detect whitespace errors.
- `cog check <range>` — validate Conventional Commit messages in a range.
- `serena project health-check .` — validate project configuration, language
  servers, and symbolic tools.
- `serena project index .` — refresh the ignored local symbol cache.
- `serena memories check` — validate namespaced memory references.

Use the owning subproject's suggested-command memory for implementation gates.
