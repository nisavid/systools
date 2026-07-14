# Context map

Systools contains multiple product contexts in one Git repository. Route work
by the paths and behavior it changes before using domain language or durable
Serena memories.

| Target | Context and memories |
| --- | --- |
| Repository-wide policy, tooling, and Serena configuration | Read `repo/*` memories. No product glossary applies. |
| `tools/mlxctl/**` | Read `tools/mlxctl/CONTEXT.md` and `mlxctl/*` memories. |
| `tools/cloud-quotas/**` | Read `tools/cloud-quotas/CONTEXT.md`. Create `cloud-quotas/*` memories only after the applicable guidance is settled. |

Treat a future nested Git repository or submodule as a separate Serena project.
Keep all ordinary subprojects beneath `tools/<tool>/` in this repository and
under the one root Serena project.
