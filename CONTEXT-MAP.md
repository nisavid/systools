# Context map

Systools contains multiple product contexts in one Git repository. Route work
by the paths and behavior it changes before using domain language or durable
Serena memories.

| Target | Context and memories |
| --- | --- |
| Repository-wide policy, tooling, and Serena configuration | Read `repo/*` memories. No product glossary applies. |
| `src/mlxctl/**`, tests that exercise `mlxctl`, and MLX deployment/research documentation | Read `src/mlxctl/CONTEXT.md` and `mlxctl/*` memories. |
| Cloud Quotas Manager work | Its owning subproject path is not settled. Do not infer a source path or create `cloud-quotas/*` memories until that decision is durable. |

Treat a future nested Git repository or submodule as a separate Serena project.
Keep all ordinary subprojects in this repository under the one root Serena
project.
