# systools

Small, focused tools for operating systems and infrastructure without turning
routine work into a pile of one-off commands.

Each tool lives in its own `tools/<tool>/` subproject with its own package,
tests, documentation, and release boundary. The repository root provides the
shared navigation and repository policy.

## Tools

| Tool | What it does | Start here |
| --- | --- | --- |
| `mlxctl` | Installs MLX runtimes and exact models, then runs multiple named local inference services behind one stable loopback Gateway. | [`tools/mlxctl/README.md`](tools/mlxctl/README.md) |

`mlxctl` is currently the only usable tool. New tools will appear here as
they become usable.

## Related projects

[Cloud Quota Manager](https://github.com/nisavid/cqmgr) is developed in its
own repository under the `cqmgr` command name. Its planning history, product
context, design context, research, and issue tracker live there.

## Explore the repository

- **Use a tool:** open its README from the table above.
- **Understand a tool:** follow the explanation and reference links from its
  README into its `docs/` directory.
- **Browse the implementation:** start in the tool's `src/` directory and use
  its tests as executable examples of behavior.
- **Contribute:** for tool-specific changes, work from the tool's subproject
  directory and run the development commands in its README. Make shared
  repository documentation and policy changes from the repository root.

A simplified view of the intentionally shallow layout:

```text
.
├── README.md
└── tools/
    └── <tool>/
        ├── README.md
        ├── docs/
        ├── src/
        └── tests/
```

## License

Systools is available under the [MIT License](LICENSE). Individual tools may
also carry a copy of the license inside their subproject.
