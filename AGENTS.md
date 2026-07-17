# Repository instructions

## Agent skills

### Issue tracker

Track issues and PRDs in GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default canonical triage labels: `needs-triage`, `needs-info`,
`ready-for-agent`, `ready-for-human`, and `wontfix`. See
`docs/agents/triage-labels.md`.

### Domain docs

Use the repository's multi-context domain layout routed by `CONTEXT-MAP.md`.
See `docs/agents/domain.md`.

## Route work by context

Read `CONTEXT-MAP.md` before planning or editing. For every changed
`tools/<tool>/` path, read that tool's `CONTEXT.md`. Root-wide work has no
product glossary unless the map assigns one. Use the selected context's
vocabulary in code, tests, documentation, issues, and commits.

Treat each `tools/<tool>/` directory as an independent product boundary. Keep
tool-specific source, tests, package metadata, locks, documentation, and
licenses inside that directory. Keep the repository root limited to shared
navigation, policy, and tooling. A new ordinary tool belongs at
`tools/<tool>/`; a nested Git repository or submodule is a separate project.

When a change crosses tool boundaries, name each affected context and validate
each one independently. Coordinate changes to external deployment contracts
with the repository that owns the deployment before publication.

## Preserve the documentation contracts

- `README.md` files are human-facing entrypoints. Route readers by goal and
  keep commands and behavioral claims verified against the current product.
- `CONTEXT.md` files define agent-facing domain language. Update them only when
  the domain model changes.
- `AGENTS.md` contains executable agent instructions. Keep each rule
  repository-specific, checkable, and in one authoritative location.
- Put detailed contracts, research, and long-form guidance under the owning
  tool's `docs/` directory.

Update the root tool index when a tool becomes usable. Use repository-relative
paths and public URLs in committed prose.

## Validate the owning subproject

Run Python checks for `tools/mlxctl/` from that directory:

```sh
uv run python -m unittest discover -s tests
uvx ruff check .
uvx ruff format --check .
uv build
```

Changes to the deployment contract also require validation in the dotfiles
repository and a scoped installation check. Changes to root Serena or context
routing require:

```sh
serena project health-check .
serena memories check
```

Every change requires `git diff --check`. Commit messages follow the
Conventional Commit policy enforced by `cog.toml` and the repository hooks.

## Keep Serena repository-scoped

Use one Serena project rooted at this Git repository. Keep checkout identity in
ignored `.serena/project.local.yml`, repository-wide memories under `repo/*`,
and tool memories under the matching tool namespace such as `mlxctl/*`.
Sibling worktrees are separate checkouts, not Serena workspace folders.
