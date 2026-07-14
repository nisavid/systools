# Domain Docs

How engineering skills should consume this repository's domain documentation.

## Before exploring, read these

- **`CONTEXT-MAP.md`** at the repository root. It routes each target path or
  planning surface to the relevant glossary and Serena memory namespace.
- The context-specific `CONTEXT.md` files named by the map.
- **`docs/adr/`** for system-wide decisions and any context-specific ADR
  directory named by the map or adjacent context documentation.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill creates them lazily when terms or decisions are resolved.

## File structure

This repository uses multiple domain contexts:

```text
/
├── CONTEXT-MAP.md
├── docs/
│   └── adr/
└── tools/
    ├── cloud-quotas/
    │   └── CONTEXT.md
    └── mlxctl/
        └── CONTEXT.md
```

Each independently shipped tool owns its implementation, tests, packaging,
product documentation, research, and context beneath `tools/<tool>/`.
Repository-wide policy, routing, hooks, CI, licensing, and Serena configuration
remain at the repository root.

## Use the glossary's vocabulary

Route the work through `CONTEXT-MAP.md`, then use the terms defined in the
selected context. If a concept is missing, either reconsider the language or
note the gap for `/domain-modeling`.

## Flag ADR conflicts

Surface contradictions with an existing ADR explicitly instead of silently overriding it.
