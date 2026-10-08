# Domain docs

## Select the context

Follow [Route work by context](../../AGENTS.md#route-work-by-context) in the
repository instructions. The root `CONTEXT-MAP.md` identifies each tool's
context file.

The engineering skills' glossary role is served by these existing `CONTEXT.md`
files.

## Read decisions when relevant

Read relevant ADRs under `docs/adr/` for shared repository decisions and
`tools/<tool>/docs/adr/` for a tool's decisions. Missing ADR directories are
normal; continue without scaffolding them. Domain modeling creates decision
documents lazily when a decision is resolved.

Surface a conflict with an existing ADR explicitly before proposing a change.
When a needed concept is absent from its context, identify the domain gap
before inventing a synonym or a new term.
