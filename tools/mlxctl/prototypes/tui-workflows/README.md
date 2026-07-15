# mlxctl TUI workflow prototype

> **THROWAWAY PROTOTYPE — no production code, persistence, or live operations.**

This prototype compares three information architectures for the feature-complete
mlxctl TUI. Every variant uses the same realistic fixture and exposes the same
operations. Controls change in-memory presentation state only.

Run it from the repository root:

```sh
python3 -m http.server 4173 --directory tools/mlxctl/prototypes/tui-workflows
```

Open <http://127.0.0.1:4173/?variant=a>.

- `a` — Operations console: resource navigation, work queue, and inspector.
- `b` — Intent launcher: task-first commands with progressive detail.
- `c` — Resource graph: model-to-runtime-to-service-to-Gateway topology.

Use the floating switcher or left and right arrow keys. Open **First run** and
**Service detail** in each variant to evaluate the workflows. Nothing writes
configuration, starts the Supervisor, installs a runtime, downloads a model, or
launches a service.
