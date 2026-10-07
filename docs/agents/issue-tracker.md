# Issue tracker: GitHub

Issues and specs for this repository live in `nisavid/systools` GitHub Issues.
Use the configured GitHub CLI wrapper when available, otherwise `gh`. Specify
`nisavid/systools` explicitly for repository-scoped operations.

## Issue lifecycle

Search for an existing issue before creating one. New requests enter with
`needs-triage`; use the role definitions in `triage-labels.md` for transitions.
Leave future work unassigned. When claiming active work, assign the issue to
the driving developer; this repository's maintainer account is `nisavid`.
Read the issue and relevant comments before changing it. Publish specs as
issues and link supporting evidence with repository-relative paths or public
URLs. Close an issue only when its stated completion criteria are met.

**PRs as a request surface: no.**

## Wayfinding and dependencies

A Wayfinder map is one issue labeled `wayfinder:map`, with a Notes,
Decisions-so-far, and Fog body. Its tickets are native GitHub sub-issues labeled
`wayfinder:research`, `wayfinder:prototype`, `wayfinder:grilling`, or
`wayfinder:task` as appropriate. Assign a claimed ticket to its driving
developer.

Use native GitHub issue dependencies for blocking relationships, including
cross-repository prerequisites. The dependent issue's `blocked_by` relation
points at the blocker's numeric database ID, not its issue number or node ID.
A frontier ticket is open, unassigned, and has no open blockers. Resolve a
ticket with the answer and evidence, close it, and add a linked decision to
its map.

If GitHub sub-issues are unavailable, put `Part of #<map>` in each child and
link the children in the map's task list. If native dependencies are
unavailable, put explicit `Blocked by` issue links in the dependent body and
check every blocker before claiming work.
