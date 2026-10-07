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

## Wayfinding operations

A Wayfinder map is one issue labeled `wayfinder:map`. Its tickets are native
GitHub sub-issues labeled `wayfinder:research`, `wayfinder:prototype`,
`wayfinder:grilling`, or `wayfinder:task`. Use Wayfinder's map body and ticket
contracts; maps and their decision tickets use only these labels rather than
the ordinary intake labels above.

The examples use `gh` as the fallback CLI. Bind `MAP` and `TICKET` to issue
numbers in this repository, `BLOCKER_URL` to the prerequisite issue's full URL,
`TICKET_LABEL` to its Wayfinder type, and the title/body variables to prepared
content. Body variables are paths to UTF-8 Markdown files.

```sh
# Create the map, then its children using the returned map number.
gh issue create --repo nisavid/systools --title "$MAP_TITLE" --body-file "$MAP_BODY" --label wayfinder:map
gh issue create --repo nisavid/systools --title "$TICKET_TITLE" --body-file "$TICKET_BODY" --label "$TICKET_LABEL" --parent "$MAP"

# Enumerate this map's children, preserving the map's order.
gh api --paginate "repos/nisavid/systools/issues/$MAP/sub_issues" --jq '.[] | {number, title, state, assignees}'

# Inspect a candidate child's blockers before claiming it.
gh issue view "$TICKET" --repo nisavid/systools --json number,state,assignees,blockedBy

# Wire a native dependency, including a cross-repository prerequisite.
gh issue edit "$TICKET" --repo nisavid/systools --add-blocked-by "$BLOCKER_URL"

# Claim before work; resolution records the answer before closure.
gh issue edit "$TICKET" --repo nisavid/systools --add-assignee nisavid
gh issue comment "$TICKET" --repo nisavid/systools --body-file "$RESOLUTION_BODY"
gh issue close "$TICKET" --repo nisavid/systools

# Append a linked decision to the freshly read map body, preserving other edits.
gh issue view "$MAP" --repo nisavid/systools --json body --jq .body
gh issue edit "$MAP" --repo nisavid/systools --body-file "$UPDATED_MAP_BODY"
```

The frontier is the open, unassigned children whose blockers are all closed.
Choose the first such child in map order. Check the current blocker states;
missing or incomplete dependency data is not evidence that a ticket is ready.
Read the map again immediately before publishing an updated body, and preserve
concurrent edits.

For `gh issue edit --add-blocked-by`, identify the blocker by issue number or
URL; use a full URL across repositories. For a direct REST request, including
`gh api`, the `issue_id` field takes the blocker's integer database ID, obtained
from the issue's REST `id` field. An issue number and GraphQL node ID are not
substitutes for that integer.

If GitHub sub-issues are unavailable, put `Part of #<map>` in each child and
link the children in the map's task list. If native dependencies are
unavailable, put explicit `Blocked by` issue links in the dependent body and
check every blocker before claiming work.
