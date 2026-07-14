# Task completion

A code change is ready when all applicable gates pass from the repository root:

1. `uv run python -m unittest discover -s tests`
2. `uvx ruff check .`
3. `uvx ruff format --check .`
4. `uv build`

For Serena configuration changes, also run:

5. `serena project health-check .`
6. `serena project index .`
7. `serena memories check`

For deployment-contract changes, validate the corresponding dotfiles templates/tests and perform a scoped live installation check. Before publication, audit task-owned paths, create a Conventional Commit, and verify the exact remote ref after the push.
