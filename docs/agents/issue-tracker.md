# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`.
- **Read an issue**: `gh issue view <number> --comments`, including labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments` with appropriate filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`.
- **Apply or remove labels**: `gh issue edit <number> --add-label "..."` or `--remove-label "..."`.
- **Close an issue**: `gh issue close <number> --comment "..."`.

Infer the repository from `git remote -v`; `gh` does this automatically inside a clone.

## Pull requests as a triage surface

**PRs as a request surface: no.**

GitHub shares one number space across issues and pull requests. Resolve an ambiguous bare number before acting on it.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.

## Wayfinding operations

The map is a single issue with child issues as tickets.

- **Map**: create one issue labelled `wayfinder:map` with Destination, Notes, Decisions so far, Not yet specified, and Out of scope sections.
- **Child ticket**: create an issue labelled `wayfinder:<type>` and link it to the map through GitHub's sub-issues endpoint. If sub-issues are unavailable, add the child to a task list in the map body and put `Part of #<map>` at the top of the child body.
- **Blocking**: use GitHub's native issue dependencies. Add an edge with `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-database-id>`, where the database ID comes from `gh api repos/<owner>/<repo>/issues/<number> --jq .id`. If dependencies are unavailable, add `Blocked by: #<number>` to the child body.
- **Frontier query**: list the map's open children, then drop issues with an open blocker or an assignee. The first remaining child in map order is the frontier.
- **Claim**: assign the ticket before work with `gh issue edit <number> --add-assignee @me`.
- **Resolve**: comment with the answer, close the issue, and append a linked one-line context pointer to the map's Decisions so far.

Refer to maps and tickets by linked title in human-facing prose, never by a bare issue number.
