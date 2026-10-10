from __future__ import annotations

import tempfile
from pathlib import Path

from mlxctl.application.dispatch import OperationRequest
from mlxctl.infrastructure.composition import compose_application
from mlxctl.infrastructure.model_supply import CacheInventory
from mlxctl.infrastructure.paths_v1 import MlxctlPaths


class _Activator:
    def __init__(self) -> None:
        self.calls = 0

    def activate(self):
        self.calls += 1


class _Port:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, operation, parameters):
        self.calls.append((operation, dict(parameters)))
        return {"operation": operation, "state": "complete"}


class _ModelSupply(_Port):
    def search(self, query, *, mode="curated", limit=20):
        return ()

    def inventory(self):
        return CacheInventory((), "local-observed", ())


class TestComposition:
    def test_uninitialized_queries_compose_without_supervisor_activation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = MlxctlPaths(
                root / "config", root / "state", root / "data", root / "logs"
            )
            activator = _Activator()
            port = _Port()
            composition = compose_application(
                paths=paths,
                activator=activator,
                runtime_supply=port,
                model_supply=_ModelSupply(),
                supervisor=port,
                setup=port,
                clients=port,
            )

            status = composition.dispatcher.execute(OperationRequest("status"))
            available = composition.dispatcher.execute(
                OperationRequest("runtime.available")
            )

            assert status.value["services"] == []
            assert isinstance(available.value["items"], (list, tuple))
            assert [item["key"] for item in available.value["items"]] == [
                "mlx_lm",
                "mlx_vlm",
                "optiq",
            ]
            assert activator.calls == 0
            assert port.calls == []

    def test_mutation_uses_the_injected_owner_and_explicit_activation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = MlxctlPaths(
                root / "config", root / "state", root / "data", root / "logs"
            )
            activator = _Activator()
            runtime = _Port()
            composition = compose_application(
                paths=paths,
                activator=activator,
                runtime_supply=runtime,
                model_supply=_ModelSupply(),
                supervisor=_Port(),
                setup=_Port(),
                clients=_Port(),
            )

            preview = composition.dispatcher.preview(
                OperationRequest("runtime.install", {"runtime": "optiq"})
            )
            result = composition.dispatcher.execute(
                OperationRequest(
                    "runtime.install",
                    {
                        "runtime": "optiq",
                        "confirmed": True,
                        "plan_fingerprint": preview.value["plan_fingerprint"],
                    },
                )
            )

            assert activator.calls == 1
            assert runtime.calls[0][0] == "runtime.install"
            assert result.value["state"] == "accepted"
