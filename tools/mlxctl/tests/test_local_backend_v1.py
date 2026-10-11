import json
from collections.abc import Callable, Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

import pytest

from mlxctl.application.catalogue import OperationKind, build_operation_catalogue
from mlxctl.application.config_schema import validate_config
from mlxctl.application.dispatch import ApplicationError, OperationRequest
from mlxctl.infrastructure.config_store import ConfigStore
from mlxctl.infrastructure.control_protocol import MAX_FRAME_BYTES
from mlxctl.infrastructure.local_backend import (
    LocalOperationBackend,
    LogReader,
    MetricsSource,
    OperationPort,
)
from mlxctl.infrastructure.model_supply import (
    CachedRevision,
    CacheInventory,
    CatalogCandidate,
    ModelHub,
    ModelRevision,
    ModelSupply,
    VerificationResult,
)
from mlxctl.infrastructure.runtime_supply import RuntimeCatalogue
from mlxctl.infrastructure.state_store import OperationalStateStore

_EMPTY_CONFIG = """\
schema_version = 1

[gateway]
host = "127.0.0.1"
port = 8766
"""

_CONFIG = """\
schema_version = 1

[gateway]
host = "127.0.0.1"
port = 8766

[runtimes."optiq-0.2.18"]
definition = "optiq"
version = "0.2.18"
provenance = "tested"
root = "/Users/example/.local/share/mlxctl/runtimes/optiq-0.2.18"
launcher = ["/Users/example/.local/share/mlxctl/runtimes/optiq-0.2.18/bin/optiq", "serve"]
capabilities = ["model", "host", "port", "kv_config", "mtp"]
bundle_id = "optiq-0.2.18-py313-macos-arm64"

[models.qwen]
repository = "mlx-community/Qwen-OptiQ"
revision = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

[aliases.coding]
installation = "qwen"

[services.coding]
model_alias = "coding"
runtime = "optiq-0.2.18"
route = "coding"
activation = "manual"
pinned = true

[services.chat]
model_alias = "coding"
runtime = "optiq-0.2.18"
route = "chat"
activation = "manual"
pinned = false

[clients.codex]
kind = "codex"
service = "coding"

[clients.codex.sampling.coding]
temperature = 0.0
"""

_MODEL_ONLY_CONFIG = """\
schema_version = 1

[models.qwen]
repository = "mlx-community/Qwen-OptiQ"
revision = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

[aliases.coding]
installation = "qwen"
"""


class _NeverCalled:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected port call: {name}")


class _Port:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {"state": "accepted"}

    def execute(self, operation, parameters):
        self.calls.append((operation, dict(parameters)))
        return self.result


class _SetupPort(_Port):
    def preview(self, parameters):
        return {
            "state": "review_required",
            "plan_fingerprint": "sha256:exact",
            "parameters": dict(parameters),
        }

    def preview_removal(self):
        return {
            "state": "review_required",
            "plan_fingerprint": "sha256:remove-exact",
            "steps": ({"id": "state.remove"},),
        }

    def remove(self, parameters):
        self.calls.append(("remove", dict(parameters)))
        return {"state": "complete", "removed": True}


class _Telemetry:
    def __init__(self, items=()):
        self.calls = []
        self.items = items

    def read(self, scope, resource=None):
        self.calls.append((scope, resource))
        return self.items

    def query(self, scope, resource=None):
        self.calls.append((scope, resource))
        return self.items


class _ModelSupply:
    revision_id = "mlx-community/Qwen-OptiQ@" + "a" * 40

    def __init__(self):
        self.calls = []

    def search(self, query, *, mode="curated", limit=20):
        self.calls.append(("search", query, mode, limit))
        return (
            CatalogCandidate(
                repo_id="mlx-community/Qwen-OptiQ",
                source="hub",
                evidence="hub-declared",
                reported_sha="a" * 40,
            ),
        )

    def resolve(self, repo_id, revision, *, offline=False):
        self.calls.append(("resolve", repo_id, revision, offline))
        return ModelRevision(repo_id, "c" * 40, revision, "hub-observed")

    def inspect_adoption(self, path):
        self.calls.append(("inspect_adoption", path))
        return {
            "path": path,
            "file_count": 2,
            "size_bytes": 42,
            "fingerprint": "f" * 64,
        }

    def inventory(self):
        return CacheInventory(
            revisions=(
                CachedRevision(
                    revision_id=self.revision_id,
                    repo_id="mlx-community/Qwen-OptiQ",
                    commit_sha="a" * 40,
                    snapshot_path=Path("/cache/qwen"),
                    size_on_disk=42,
                    evidence="local-observed",
                    complete=True,
                ),
            ),
            evidence="local-observed",
            warnings=(),
        )

    def execute(self, operation, parameters):
        self.calls.append((operation, dict(parameters)))
        return {"job": "model-job", "state": "queued"}

    def verify(self, installation):
        self.calls.append(("verify", installation.installation_id))
        return VerificationResult("complete", "cache-completeness", ())


class _ModelIntelligence:
    def __init__(self):
        self.calls = []

    def inspect(self, repository, revision, **scenario) -> dict[str, object]:
        self.calls.append((repository, revision, scenario))
        return {"identity": {"repo_id": repository, "commit_sha": "b" * 40}}


class _DirectInstallSupply(_ModelSupply):
    # Exercise the fallback when this optional operation entry is non-callable.
    execute = cast(Callable[..., dict[str, str]], None)

    def install(self, **parameters):
        self.calls.append(("install", parameters))
        return {"alias": parameters["alias"]}


class TestLocalOperationBackend:
    def _backend(self, root: Path, config: str = _CONFIG, **ports):
        config_path = root / "config.toml"
        config_path.write_text(config, encoding="utf-8")
        state = OperationalStateStore(root / "state.sqlite3")
        backend = LocalOperationBackend(
            catalogue=build_operation_catalogue(),
            config_store=ConfigStore(config_path, validate_config),
            state_store=state,
            runtime_catalogue=RuntimeCatalogue.load_builtin(),
            runtime_supply=ports.get("runtime_supply", _NeverCalled()),
            model_supply=ports.get(
                "model_supply", ModelSupply(cast(ModelHub, _NeverCalled()))
            ),
            supervisor=ports.get("supervisor", _NeverCalled()),
            logs=ports.get("logs", _NeverCalled()),
            metrics=ports.get("metrics", _NeverCalled()),
            setup=ports.get("setup", _NeverCalled()),
            clients=ports.get("clients", _NeverCalled()),
            config_path=config_path,
            model_intelligence=ports.get("model_intelligence", _ModelIntelligence()),
        )
        return backend, state

    def test_empty_status_reports_stopped_without_activating_supervisor(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(_EMPTY_CONFIG, encoding="utf-8")
            backend = LocalOperationBackend(
                catalogue=build_operation_catalogue(),
                config_store=ConfigStore(config_path, validate_config),
                state_store=OperationalStateStore(root / "state.sqlite3"),
                runtime_catalogue=RuntimeCatalogue.load_builtin(),
                runtime_supply=cast(OperationPort, _NeverCalled()),
                model_supply=ModelSupply(cast(ModelHub, _NeverCalled())),
                supervisor=cast(OperationPort, _NeverCalled()),
                logs=cast(LogReader, _NeverCalled()),
                metrics=cast(MetricsSource, _NeverCalled()),
                setup=cast(OperationPort, _NeverCalled()),
                clients=cast(OperationPort, _NeverCalled()),
                config_path=config_path,
            )

            prepared = backend.prepare(OperationRequest("status"))
            result = prepared.execute()

            assert not prepared.requires_supervisor
            assert result["schema_version"] == 1
            assert isinstance(result["supervisor"], Mapping)
            assert result["supervisor"]["state"] == "stopped"
            assert result["services"] == []
            assert result["operations"] == []
            assert result["active_operations"] == 0
            assert result["pressure"] == "unknown"
            assert isinstance(result["next_actions"], (list, tuple, Mapping, str))
            assert "mlxctl supervisor start" in result["next_actions"]

    def test_uninitialized_status_and_config_are_actionable_without_a_file(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            state = OperationalStateStore(root / "state.sqlite3")
            backend = LocalOperationBackend(
                catalogue=build_operation_catalogue(),
                config_store=ConfigStore(config_path, validate_config),
                state_store=state,
                runtime_catalogue=RuntimeCatalogue.load_builtin(),
                runtime_supply=cast(OperationPort, _NeverCalled()),
                model_supply=ModelSupply(cast(ModelHub, _NeverCalled())),
                supervisor=cast(OperationPort, _NeverCalled()),
                logs=cast(LogReader, _NeverCalled()),
                metrics=cast(MetricsSource, _NeverCalled()),
                setup=cast(OperationPort, _NeverCalled()),
                clients=cast(OperationPort, _NeverCalled()),
                config_path=config_path,
            )

            status = backend.prepare(OperationRequest("status")).execute()
            shown = backend.prepare(OperationRequest("config.show")).execute()

            assert status["services"] == []
            assert shown["state"] == "uninitialized"
            assert isinstance(shown["next_actions"], (list, tuple, Mapping, str))
            assert "mlxctl setup" in shown["next_actions"]
            assert not config_path.exists()

    def test_service_list_keeps_desired_and_run_state_distinct(self) -> None:
        with TemporaryDirectory() as directory:
            backend, state = self._backend(Path(directory))
            state.put_snapshot(
                {
                    "kind": "service_run",
                    "id": "run-1",
                    "version": 1,
                    "service": "coding",
                    "state": "ready",
                    "upstream_port": 49152,
                }
            )

            result = backend.prepare(OperationRequest("service.list")).execute()

            assert isinstance(result["items"], (list, tuple))
            assert [item["name"] for item in result["items"]] == ["chat", "coding"]
            coding = next(item for item in result["items"] if item["name"] == "coding")
            assert coding["desired"]["pinned"]
            assert coding["run"]["id"] == "run-1"
            assert isinstance(result["items"], (list, tuple))
            chat = next(item for item in result["items"] if item["name"] == "chat")
            assert chat["run"] is None

    def test_diagnostic_queries_have_distinct_user_facing_results(self) -> None:
        with TemporaryDirectory() as directory:
            clients = _Port({"state": "healthy", "next_actions": []})
            backend, state = self._backend(Path(directory), clients=clients)
            state.put_snapshot(
                {
                    "kind": "supervisor",
                    "id": "supervisor",
                    "version": 1,
                    "state": "running",
                }
            )
            state.put_snapshot(
                {
                    "kind": "gateway",
                    "id": "gateway",
                    "version": 1,
                    "state": "running",
                    "port": 9999,
                }
            )
            state.put_snapshot(
                {
                    "kind": "service_run",
                    "id": "run-1",
                    "version": 1,
                    "service": "coding",
                    "state": "unhealthy",
                }
            )
            state.put_operation({"id": "job-1", "status": "running"})

            status = backend.prepare(OperationRequest("status")).execute()
            check = backend.prepare(OperationRequest("check")).execute()
            doctor = backend.prepare(OperationRequest("doctor")).execute()
            supervisor = backend.prepare(
                OperationRequest("supervisor.inspect")
            ).execute()
            gateway_status = backend.prepare(
                OperationRequest("gateway.status")
            ).execute()
            gateway_inspect = backend.prepare(
                OperationRequest("gateway.inspect")
            ).execute()
            routes = backend.prepare(OperationRequest("gateway.routes")).execute()
            runtime = backend.prepare(OperationRequest("runtime.doctor")).execute()
            service = backend.prepare(
                OperationRequest("service.check", {"resource": "coding"})
            ).execute()

            assert "checks" not in status
            assert isinstance(check["checks"], (list, tuple))
            assert check["checks"][0]["name"] == "supervisor"
            assert not doctor["healthy"]
            assert isinstance(doctor["issues"], (list, tuple))
            assert {issue["code"] for issue in doctor["issues"]} == {
                "gateway_drift",
                "service_unhealthy",
            }
            assert ("client.inspect", {"client": "codex"}) in clients.calls
            assert isinstance(supervisor["operations"], (list, tuple))
            assert supervisor["operations"][0]["id"] == "job-1"
            assert gateway_status["route_count"] == 2
            assert "routes" not in gateway_status
            assert isinstance(gateway_inspect["routes"], (list, tuple, Mapping, str))
            assert len(gateway_inspect["routes"]) == 2
            assert isinstance(routes["items"], (list, tuple))
            assert {item["service"] for item in routes["items"]} == {"chat", "coding"}
            assert isinstance(runtime["items"], (list, tuple))
            assert runtime["items"][0]["state"] == "missing"
            assert isinstance(service["checks"], (list, tuple))
            assert service["checks"][2]["state"] == "unavailable"

    def test_doctor_reports_codex_context_drift_from_service_cap(self) -> None:
        config = (
            _CONFIG.replace("[services.coding]", "[services.coding_internal]")
            .replace(
                "pinned = true\n\n[services.chat]",
                "pinned = true\n\n[services.coding_internal.options]\nmax_context = 131072\n\n[services.chat]",
            )
            .replace(
                '[clients.codex]\nkind = "codex"\nservice = "coding"',
                '[clients.codex]\nkind = "codex"\nservice = "coding_internal"\ncontext_window = 196608',
            )
        )
        with TemporaryDirectory() as directory:
            backend, _state = self._backend(
                Path(directory),
                config=config,
                clients=_Port({"state": "healthy", "next_actions": []}),
            )

            doctor = backend.prepare(OperationRequest("doctor")).execute()

            assert isinstance(doctor["issues"], (list, tuple))
            assert "codex_context_drift" in {
                issue["code"] for issue in doctor["issues"]
            }
            assert "codex_catalog_unknown" not in {
                issue["code"] for issue in doctor["issues"]
            }

    def test_strict_resource_lookup_reports_unknown_service(self) -> None:
        with TemporaryDirectory() as directory:
            backend, _ = self._backend(Path(directory))

            with pytest.raises(ApplicationError) as raised:
                backend.prepare(
                    OperationRequest("service.inspect", {"resource": "missing"})
                ).execute()

            assert raised.value.code == "resource_not_found"

    def test_empty_metrics_are_reported_as_absent_not_invented(self) -> None:
        with TemporaryDirectory() as directory:
            metrics = _Telemetry()
            backend, _ = self._backend(Path(directory), metrics=metrics)

            result = backend.prepare(OperationRequest("metrics")).execute()

            assert result["items"] == []
            assert result["evidence"] == ["no-metrics-observed"]
            assert metrics.calls == [("all", None)]

            backend.prepare(
                OperationRequest("metrics", {"resource": "coding"})
            ).execute()
            assert metrics.calls[-1] == ("all", "coding")

    def test_model_search_uses_the_cli_source_and_install_derives_alias(self) -> None:
        with TemporaryDirectory() as directory:
            supply = _DirectInstallSupply()
            backend, _ = self._backend(Path(directory), model_supply=supply)

            backend.prepare(
                OperationRequest(
                    "model.search",
                    {"query": "Qwen", "source": "broad", "limit": 3},
                )
            ).execute()
            backend.prepare(
                OperationRequest(
                    "model.install",
                    {
                        "repository": "mlx-community/Qwen-OptiQ",
                        "revision": "a" * 40,
                    },
                )
            ).execute()

            assert ("search", "Qwen", "broad", 3) in supply.calls
            install = next(call for call in supply.calls if call[0] == "install")
            assert install[1]["alias"] == "Qwen-OptiQ"

    def test_model_install_executes_the_exact_revision_bound_in_preview(self) -> None:
        with TemporaryDirectory() as directory:
            supply = _ModelSupply()
            backend, _ = self._backend(Path(directory), model_supply=supply)
            request = OperationRequest(
                "model.install",
                {
                    "repository": "mlx-community/Qwen-OptiQ",
                    "revision": "main",
                },
            )
            preview = backend.prepare(request).events[-1]

            backend.prepare(
                OperationRequest(
                    "model.install",
                    {
                        **dict(request.parameters),
                        "confirmed": True,
                        "plan_fingerprint": preview["plan_fingerprint"],
                    },
                )
            ).execute()

            execution = next(
                call for call in supply.calls if call[0] == "model.install"
            )
            assert execution[1]["revision"] == "c" * 40

    def test_model_adopt_binds_execution_to_previewed_snapshot_fingerprint(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            supply = _ModelSupply()
            backend, _ = self._backend(Path(directory), model_supply=supply)
            request = OperationRequest(
                "model.adopt",
                {
                    "repository": "mlx-community/Qwen-OptiQ",
                    "revision": "a" * 40,
                    "path": "/Volumes/models/qwen",
                },
            )
            preview = backend.prepare(request).events[-1]

            backend.prepare(
                OperationRequest(
                    "model.adopt",
                    {
                        **dict(request.parameters),
                        "confirmed": True,
                        "plan_fingerprint": preview["plan_fingerprint"],
                    },
                )
            ).execute()

            execution = next(call for call in supply.calls if call[0] == "model.adopt")
            assert execution[1]["snapshot_fingerprint"] == "f" * 64
            assert execution[1]["path"] == "/Volumes/models/qwen"

    def test_model_trust_is_exact_revision_and_runtime_scoped(self) -> None:
        with TemporaryDirectory() as directory:
            backend, state = self._backend(Path(directory))

            result = backend.prepare(
                OperationRequest(
                    "model.trust",
                    {
                        "resource": "coding",
                        "runtime": "optiq-0.2.18",
                        "accepted_risks": ["custom_code"],
                    },
                )
            ).execute()

            trust = result["resource"]
            assert isinstance(trust, Mapping)
            assert trust["model_installation"] == "qwen"
            assert trust["runtime_installation"] == "optiq-0.2.18"
            assert trust["revision"] == "a" * 40
            assert trust["accepted_risks"] == ["custom_code"]
            assert (
                state.snapshot("trust", "qwen@optiq-0.2.18", version="a" * 40)
                is not None
            )

            with pytest.raises(ApplicationError, match="JSON array"):
                backend.prepare(
                    OperationRequest(
                        "model.trust",
                        {
                            "resource": "coding",
                            "runtime": "optiq-0.2.18",
                            "accepted_risks": "custom_code",
                        },
                    )
                ).execute()

    def test_model_inspect_accepts_arbitrary_repository_before_install(self) -> None:
        with TemporaryDirectory() as directory:
            intelligence = _ModelIntelligence()
            backend, _ = self._backend(Path(directory), model_intelligence=intelligence)

            result = backend.prepare(
                OperationRequest(
                    "model.inspect",
                    {
                        "repository": "mlx-community/New-OptiQ",
                        "revision": "main",
                        "context_tokens": 65536,
                        "concurrency": 2,
                    },
                )
            ).execute()

            assert intelligence.calls[0][0] == "mlx-community/New-OptiQ"
            assert intelligence.calls[0][2]["context_tokens"] == 65536
            assert isinstance(result["resource"], Mapping)
            assert result["resource"]["identity"]["commit_sha"] == "b" * 40

    def test_model_inspect_summarizes_large_repository_manifest(self) -> None:
        class LargeManifestIntelligence(_ModelIntelligence):
            def inspect(self, repository, revision, **scenario):
                report = super().inspect(repository, revision, **scenario)
                report["repository_files"] = [
                    {
                        "path": f"{'nested-' * 30}{index:05}.bin",
                        "size": index,
                        "blob_id": "a" * 40,
                    }
                    for index in range(5_000)
                ]
                return report

        with TemporaryDirectory() as directory:
            backend, _ = self._backend(
                Path(directory), model_intelligence=LargeManifestIntelligence()
            )

            result = backend.prepare(
                OperationRequest(
                    "model.inspect",
                    {"repository": "owner/large", "revision": "main"},
                )
            ).execute()

            resource = result["resource"]
            assert isinstance(resource, Mapping)
            assert resource["repository_file_count"] == 5_000
            assert len(resource["repository_manifest_sha256"]) == 64
            assert isinstance(resource, (list, tuple, Mapping, str))
            assert "repository_files" not in resource
            assert (
                len(json.dumps(result, separators=(",", ":")).encode())
                < MAX_FRAME_BYTES
            )

    def test_config_import_reads_a_bounded_explicit_source(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            backend, _ = self._backend(root)
            source = root / "candidate.toml"
            source.write_text(_CONFIG.replace("port = 8766", "port = 9000"))

            request = OperationRequest("config.import", {"source": str(source)})
            preview = backend.prepare(request).events[-1]
            result = backend.prepare(
                OperationRequest(
                    "config.import",
                    {
                        "source": str(source),
                        "confirmed": True,
                        "plan_fingerprint": preview["plan_fingerprint"],
                    },
                )
            ).execute()

            assert isinstance(result["resource"], Mapping)
            assert result["resource"]["value"]["gateway"]["port"] == 9000

    def test_every_confirmed_mutation_is_bound_to_current_config_revision(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            backend, _ = self._backend(Path(directory))
            request = OperationRequest("service.remove", {"resource": "chat"})
            fingerprint = backend.prepare(request).events[-1]["plan_fingerprint"]

            backend._config_store.edit(
                lambda document: document["gateway"].update({"port": 9000})
            )

            with pytest.raises(ApplicationError, match="plan changed") as caught:
                backend.prepare(
                    OperationRequest(
                        "service.remove",
                        {
                            "resource": "chat",
                            "confirmed": True,
                            "plan_fingerprint": fingerprint,
                        },
                    )
                )
            assert caught.value.code == "stale_plan"

    def test_local_service_edit_has_preview_and_never_uses_supervisor(self) -> None:
        with TemporaryDirectory() as directory:
            supervisor = _Port()
            backend, _ = self._backend(Path(directory), supervisor=supervisor)

            prepared = backend.prepare(
                OperationRequest(
                    "service.edit",
                    {"resource": "chat", "pinned": True},
                )
            )
            result = prepared.execute()

            assert not prepared.requires_supervisor
            assert prepared.events[0]["confirmation_required"]
            assert isinstance(result["preview"], Mapping)
            assert result["preview"]["operation"] == "service.edit"
            assert supervisor.calls == []
            inspected = backend.prepare(
                OperationRequest("service.inspect", {"resource": "chat"})
            ).execute()
            assert isinstance(inspected["resource"], Mapping)
            assert inspected["resource"]["desired"]["pinned"]

    def test_first_local_mutation_initializes_minimal_desired_state(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            backend, _ = self._backend(root, config=_EMPTY_CONFIG)
            (root / "config.toml").unlink()

            backend.prepare(
                OperationRequest("gateway.configure", {"port": 9001})
            ).execute()

            assert (root / "config.toml").exists()
            shown = backend.prepare(OperationRequest("config.show")).execute()
            assert isinstance(shown["resource"], Mapping)
            assert shown["resource"]["gateway"]["port"] == 9001

    def test_service_remove_drains_and_stops_before_deleting_desired_state(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            supervisor = _Port()
            backend, _ = self._backend(Path(directory), supervisor=supervisor)

            prepared = backend.prepare(
                OperationRequest("service.remove", {"resource": "chat"})
            )
            result = prepared.execute()

            assert prepared.requires_supervisor
            assert [call[0] for call in supervisor.calls] == ["service.remove"]
            assert isinstance(result["resource"], Mapping)
            assert result["resource"]["service"] == "chat"
            remaining = backend.prepare(OperationRequest("service.list")).execute()
            assert isinstance(remaining["items"], (list, tuple))
            assert "chat" not in {item["name"] for item in remaining["items"]}

    def test_model_uninstall_removes_unreferenced_alias_with_installation(self) -> None:
        with TemporaryDirectory() as directory:
            backend, _ = self._backend(Path(directory), config=_MODEL_ONLY_CONFIG)

            backend.prepare(
                OperationRequest("model.uninstall", {"resource": "coding"})
            ).execute()

            shown = backend.prepare(OperationRequest("config.show")).execute()
            assert isinstance(shown["resource"], Mapping)
            assert shown["resource"]["models"] == {}
            assert shown["resource"]["aliases"] == {}

    def test_model_uninstall_only_unregisters_adopted_external_bytes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "external"
            snapshot.mkdir()
            marker = snapshot / "weights.safetensors"
            marker.write_bytes(b"externally owned")
            config = _MODEL_ONLY_CONFIG.replace(
                'revision = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
                'revision = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n'
                f'provenance = "adopted"\npath = "{snapshot}"',
            )
            backend, _ = self._backend(root, config=config)

            backend.prepare(
                OperationRequest("model.uninstall", {"resource": "coding"})
            ).execute()

            assert marker.read_bytes() == b"externally owned"
            shown = backend.prepare(OperationRequest("config.show")).execute()
            assert isinstance(shown["resource"], Mapping)
            assert shown["resource"]["models"] == {}

    def test_model_inspect_reports_the_adopted_external_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "external"
            snapshot.mkdir()
            config = _MODEL_ONLY_CONFIG.replace(
                'revision = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
                'revision = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n'
                f'provenance = "adopted"\npath = "{snapshot}"',
            )
            backend, _ = self._backend(
                root,
                config=config,
                model_supply=_ModelSupply(),
                model_intelligence=_ModelIntelligence(),
            )

            result = backend.prepare(
                OperationRequest("model.inspect", {"resource": "coding"})
            ).execute()

            assert isinstance(result["resource"], Mapping)
            assert (
                result["resource"]["installation"]["provenance"] == "external-adopted"
            )
            assert result["resource"]["installation"]["snapshot"]["path"] == str(
                snapshot
            )
            assert isinstance(result["evidence"], (list, tuple, Mapping, str))
            assert "external-adopted-snapshot" in result["evidence"]

    def test_service_create_uses_the_public_service_argument(self) -> None:
        with TemporaryDirectory() as directory:
            backend, _ = self._backend(Path(directory))

            backend.prepare(
                OperationRequest(
                    "service.create",
                    {
                        "service": "assistant",
                        "model_alias": "coding",
                        "runtime": "optiq-0.2.18",
                        "route": "assistant",
                    },
                )
            ).execute()

            listed = backend.prepare(OperationRequest("service.list")).execute()
            assert isinstance(listed["items"], (list, tuple))
            assert "assistant" in [item["name"] for item in listed["items"]]

    def test_live_lifecycle_calls_only_supervisor_port(self) -> None:
        with TemporaryDirectory() as directory:
            supervisor = _Port({"run_id": "run-2", "state": "starting"})
            runtime = _Port()
            model = _ModelSupply()
            backend, _ = self._backend(
                Path(directory),
                supervisor=supervisor,
                runtime_supply=runtime,
                model_supply=model,
            )

            prepared = backend.prepare(
                OperationRequest("service.start", {"resource": "coding"})
            )
            result = prepared.execute()

            assert prepared.requires_supervisor
            assert supervisor.calls == [("service.start", {"resource": "coding"})]
            assert runtime.calls == []
            assert model.calls == []
            assert isinstance(result["resource"], Mapping)
            assert result["resource"]["run_id"] == "run-2"

    def test_client_probe_content_is_forwarded_but_never_persisted(self) -> None:
        with TemporaryDirectory() as directory:
            clients = _Port({"response": "ephemeral"})
            backend, state = self._backend(Path(directory), clients=clients)

            result = backend.prepare(
                OperationRequest(
                    "client.test",
                    {"resource": "codex", "prompt": "say hello"},
                )
            ).execute()

            assert isinstance(result["resource"], Mapping)
            assert result["resource"]["response"] == "ephemeral"
            assert state.operations() == ()
            assert state.events() == ()

    def test_setup_prepares_exact_plan_and_defers_activation_to_remote_steps(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            setup = _SetupPort()
            backend, _ = self._backend(Path(directory), setup=setup)

            prepared = backend.prepare(OperationRequest("setup"))

            assert not prepared.requires_supervisor
            assert prepared.events[0]["plan_fingerprint"] == "sha256:exact"
            assert setup.calls == []

    def test_product_removal_previews_exact_plan_without_starting_supervisor(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            setup = _SetupPort()
            backend, _ = self._backend(Path(directory), setup=setup)

            prepared = backend.prepare(OperationRequest("remove"))

            assert not prepared.requires_supervisor
            assert prepared.events[0]["plan_fingerprint"] == "sha256:remove-exact"
            result = prepared.execute()
            assert isinstance(result["resource"], Mapping)
            assert result["resource"]["removed"]
            assert setup.calls == [("remove", {})]

    def test_runtime_and_model_jobs_call_only_their_supply_ports(self) -> None:
        with TemporaryDirectory() as directory:
            runtime = _Port({"job": "runtime-job"})
            model = _ModelSupply()
            supervisor = _Port()
            backend, _ = self._backend(
                Path(directory),
                runtime_supply=runtime,
                model_supply=model,
                supervisor=supervisor,
            )

            runtime_result = backend.prepare(
                OperationRequest("runtime.update", {"resource": "optiq-0.2.18"})
            ).execute()
            model_result = backend.prepare(
                OperationRequest(
                    "model.repair",
                    {"resource": "qwen"},
                )
            ).execute()

            assert isinstance(runtime_result["resource"], Mapping)
            assert runtime_result["resource"]["job"] == "runtime-job"
            assert isinstance(model_result["resource"], Mapping)
            assert model_result["resource"]["job"] == "model-job"
            assert runtime.calls[0][0] == "runtime.update"
            assert model.calls[0][0] == "model.repair"
            assert supervisor.calls == []

    def test_model_verify_uses_exact_installed_revision_and_cache(self) -> None:
        with TemporaryDirectory() as directory:
            model = _ModelSupply()
            backend, _ = self._backend(Path(directory), model_supply=model)

            result = backend.prepare(
                OperationRequest("model.verify", {"resource": "coding"})
            ).execute()

            assert isinstance(result["resource"], Mapping)
            assert result["resource"]["status"] == "complete"
            assert ("verify", "qwen") in model.calls
            assert result["evidence"] == ["cache-completeness"]

    def test_every_catalogue_entry_prepares_with_realistic_prerequisites(
        self, subtests: pytest.Subtests
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            backend, state = self._backend(
                root,
                runtime_supply=_Port(),
                model_supply=_ModelSupply(),
                supervisor=_Port(),
                logs=_Telemetry(),
                metrics=_Telemetry(),
                setup=_Port(),
                clients=_Port(),
            )
            state.put_operation({"id": "job-1", "state": "running"})
            for name in build_operation_catalogue():
                with subtests.test(operation=name):
                    prepared = backend.prepare(
                        OperationRequest(name, self._parameters(name))
                    )
                    assert prepared.execute is not None

    def test_every_query_executes_without_supervisor_activation(
        self, subtests: pytest.Subtests
    ) -> None:
        with TemporaryDirectory() as directory:
            backend, state = self._backend(
                Path(directory),
                runtime_supply=_Port(),
                model_supply=_ModelSupply(),
                supervisor=_NeverCalled(),
                logs=_Telemetry(),
                metrics=_Telemetry(),
                setup=_Port(),
                clients=_Port(),
            )
            state.put_operation({"id": "job-1", "state": "running"})
            for name, operation in build_operation_catalogue().items():
                if operation.kind is not OperationKind.QUERY:
                    continue
                with subtests.test(operation=name):
                    prepared = backend.prepare(
                        OperationRequest(name, self._parameters(name))
                    )
                    result = prepared.execute()
                    assert not prepared.requires_supervisor
                    assert result["schema_version"] == 1
                    assert result["operation"] == name

    def test_mutation_categories_have_preview_and_exact_activation_policy(
        self, subtests: pytest.Subtests
    ) -> None:
        supervisor_backed = {
            "supervisor.start",
            "supervisor.stop",
            "supervisor.restart",
            "gateway.restart",
            "runtime.install",
            "runtime.adopt",
            "runtime.update",
            "runtime.rollback",
            "runtime.remove",
            "runtime.prune",
            "model.install",
            "model.adopt",
            "model.repair",
            "model.update",
            "model.rollback",
            "model.cache.evict",
            "model.cache.prune",
            "service.start",
            "service.stop",
            "service.restart",
            "service.remove",
        }
        with TemporaryDirectory() as directory:
            backend, state = self._backend(
                Path(directory),
                model_supply=_ModelSupply(),
                setup=_SetupPort(),
            )
            state.put_operation({"id": "job-1", "state": "running"})
            for name, operation in build_operation_catalogue().items():
                if operation.kind is not OperationKind.MUTATION:
                    continue
                with subtests.test(operation=name):
                    prepared = backend.prepare(
                        OperationRequest(name, self._parameters(name))
                    )
                    assert prepared.requires_supervisor == (name in supervisor_backed)
                    assert prepared.events[0]["phase"] == "preview"
                    assert str(prepared.events[0]["plan_fingerprint"]).startswith(
                        "sha256:"
                    )
                    assert (
                        prepared.events[0]["confirmation_required"]
                        == operation.confirmation
                    )

    @staticmethod
    def _parameters(name):
        if name.startswith("runtime."):
            if name in {"runtime.install", "runtime.adopt", "runtime.available"}:
                return {"runtime": "optiq"}
            return {"resource": "optiq-0.2.18"}
        if name == "model.search":
            return {"query": "Qwen"}
        if name.startswith("model.cache."):
            return {"resource": _ModelSupply.revision_id}
        if name.startswith("model."):
            return {
                "resource": "qwen",
                "alias": "coding",
                "repository": "mlx-community/Qwen-OptiQ",
                "revision": "a" * 40,
                "path": "/tmp/external-model",
            }
        if name.startswith("service."):
            return {"resource": "coding"}
        if name.startswith("operation."):
            return {"resource": "job-1"}
        if name.startswith("client."):
            return {"resource": "codex"}
        if name == "config.diff":
            return {"text": _CONFIG}
        if name == "config.import":
            return {"text": _CONFIG}
        if name == "config.restore":
            return {"revision": "a" * 64}
        return {}
