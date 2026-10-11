from __future__ import annotations

import asyncio
import plistlib
import socket
import stat
import tempfile
import threading
import time
from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import cast

import httpx
import pytest
from huggingface_hub import scan_cache_dir

from mlxctl.application.config_schema import validate_config
from mlxctl.application.dispatch import ApplicationError, OperationRequest
from mlxctl.application.dispatch import Dispatcher as OperationDispatcher
from mlxctl.application.setup import SetupPreflight
from mlxctl.infrastructure.config_store import ConfigStore
from mlxctl.infrastructure.control_protocol import UnixControlServer
from mlxctl.infrastructure.daemon_service import DaemonOperationRouter, DaemonService
from mlxctl.infrastructure.gateway_credential import GatewayCredential
from mlxctl.infrastructure.host_integration import LaunchdSupervisorActivator
from mlxctl.infrastructure.launchd import LaunchdAdapter
from mlxctl.infrastructure.model_supply import (
    CachedRevision,
    CacheInventory,
    ModelSupply,
)
from mlxctl.infrastructure.paths_v1 import MlxctlPaths
from mlxctl.infrastructure.production import (
    _ActivatingOperationOwner,
    _GatewayMutationGuard,
    _LocalModelSupply,
    _LocalSupervisorOwner,
    _sampling_matches_service_model,
    _setup_planner,
    _SetupSupervisorOwner,
    compose_daemon,
    compose_local,
    make_launchd,
)
from mlxctl.infrastructure.production_host import (
    AbsoluteUvRunner,
    GatewayVerificationPort,
    OwnedStateRemover,
    client_request,
    coherent_client_context,
    configured_model_installations,
    default_sampling,
    resolve_uv,
)
from mlxctl.infrastructure.state_store import OperationalStateStore
from mlxctl.infrastructure.supply_ports import ExactRevisionModelSecurity


class _Port:
    def __init__(self, result=None) -> None:
        self.calls = []
        self.result = result or {"state": "running"}

    def execute(self, operation, parameters):
        self.calls.append((operation, dict(parameters)))
        return dict(self.result)


class _Activator:
    def __init__(self) -> None:
        self.calls = 0

    def activate(self) -> None:
        self.calls += 1


class _LaunchdStatus:
    def __init__(self, running: bool, registered: bool = True) -> None:
        self.running = running
        self.registered = registered


class _Launchd:
    def __init__(self, running: bool) -> None:
        self.running = running
        self.registered = True
        self.bootout_calls = 0

    def status(self):
        return _LaunchdStatus(self.running, self.registered)

    def bootout(self):
        self.bootout_calls += 1
        self.running = False
        self.registered = False
        return self.status()


class TestProductionComposition:
    @staticmethod
    def _profile_binding_config(*, revision: str):
        return validate_config(
            {
                "schema_version": 1,
                "runtimes": {
                    "optiq@0.3.3": {
                        "definition": "optiq",
                        "version": "0.3.3",
                        "provenance": "tested",
                        "root": "/tmp/optiq",
                        "launcher": ["/tmp/optiq/bin/optiq", "serve"],
                        "capabilities": ["model"],
                    }
                },
                "models": {
                    "qwen": {
                        "repository": "mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit",
                        "revision": revision,
                    }
                },
                "aliases": {"qwen": {"installation": "qwen"}},
                "services": {
                    "coding": {
                        "model_alias": "qwen",
                        "runtime": "optiq@0.3.3",
                        "route": "coding",
                    }
                },
            }
        )

    def test_client_context_defaults_to_and_enforces_service_cap(self) -> None:
        assert coherent_client_context(131_072, None) == 131_072
        assert coherent_client_context(131_072, 131_072) == 131_072
        with pytest.raises(ValueError, match="must match"):
            coherent_client_context(131_072, 196_608)

    def test_local_model_resolution_is_side_effect_free_and_stays_local(self) -> None:
        class Supply:
            def resolve(self, repo_id, revision, *, offline=False):
                return (repo_id, revision, offline)

        remote = _Port()
        model = _LocalModelSupply(
            cast(ModelSupply, Supply()),
            remote,
            cast(ExactRevisionModelSecurity, object()),
        )

        assert model.resolve("owner/model", "main", offline=True) == (
            "owner/model",
            "main",
            True,
        )
        assert remote.calls == []

    def test_local_composition_prepares_private_paths_before_store_construction(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = MlxctlPaths(
                root / "config", root / "state", root / "data", root / "logs"
            )

            prepared = []
            prepare = MlxctlPaths.prepare

            def record_prepare(instance):
                prepared.append(instance)
                prepare(instance)

            monkeypatch.setattr(MlxctlPaths, "prepare", record_prepare)
            compose_local(paths=paths, home=root, executable=Path("/usr/bin/python3"))

            assert len(prepared) >= 1

    def test_setup_remote_owner_activates_only_at_execution_boundary(self) -> None:
        activator = _Activator()
        remote = _Port({"state": "complete"})
        owner = _ActivatingOperationOwner(
            cast(LaunchdSupervisorActivator, activator), remote
        )

        assert activator.calls == 0
        owner.execute("runtime.install", {"runtime": "optiq"})

        assert activator.calls == 1
        assert remote.calls[0][0] == "runtime.install"

    def test_setup_supervisor_activation_is_visible_and_idempotently_forwarded(
        self,
    ) -> None:
        activator = _Activator()
        remote = _Port({"state": "running"})

        owner = _SetupSupervisorOwner(
            remote,
            cast(LaunchdAdapter, _Launchd(running=False)),
            cast(LaunchdSupervisorActivator, activator),
        )
        result = owner.execute("supervisor.start", {})

        assert activator.calls == 1
        assert remote.calls == [("supervisor.start", {})]
        assert result["state"] == "running"

    def test_setup_recycles_a_running_supervisor_before_loading_new_code(self) -> None:
        activator = _Activator()
        remote = _Port({"state": "running"})
        launchd = _Launchd(running=True)

        owner = _SetupSupervisorOwner(
            remote,
            cast(LaunchdAdapter, launchd),
            cast(LaunchdSupervisorActivator, activator),
        )
        result = owner.execute("supervisor.start", {})

        assert launchd.bootout_calls == 1
        assert activator.calls == 1
        assert remote.calls == [("supervisor.start", {})]
        assert result["state"] == "running"

    def test_request_profile_is_bound_to_the_service_exact_model_revision(self) -> None:
        exact_revision = "70a3aa32c7feef511182bf16aa332f37e8d82014"
        config = self._profile_binding_config(revision=exact_revision)
        sampling = default_sampling(
            "mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit",
            exact_revision,
            "codex",
        )["coding"]

        assert _sampling_matches_service_model(
            config, config.services["coding"], sampling
        )

        other = self._profile_binding_config(revision="a" * 40)
        assert not _sampling_matches_service_model(
            other, other.services["coding"], sampling
        )

    def test_local_supervisor_stop_is_idempotent_without_remote_activation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote = _Port({"state": "stopping"})
            owner = _LocalSupervisorOwner(
                remote,
                cast(LaunchdAdapter, _Launchd(running=False)),
                root / "mlxd.sock",
                OperationalStateStore(root / "state.db"),
                ConfigStore(root / "config.toml", validate_config),
            )

            result = owner.execute("supervisor.stop", {})

        assert result == {"state": "stopped", "already_stopped": True}
        assert remote.calls == []

    def test_local_supervisor_stop_forwards_when_launchd_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote = _Port({"state": "stopping"})
            owner = _LocalSupervisorOwner(
                remote,
                cast(LaunchdAdapter, _Launchd(running=True)),
                root / "mlxd.sock",
                OperationalStateStore(root / "state.db"),
                ConfigStore(root / "config.toml", validate_config),
            )

            result = owner.execute("supervisor.stop", {})

        assert result == {"state": "stopping"}
        assert remote.calls == [("supervisor.stop", {})]

    def test_local_supervisor_stop_forwards_to_foreground_socket_owner(
        self, cleanup
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "mlxd.sock"
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            cleanup.callback(listener.close)
            listener.bind(str(path))
            listener.listen(1)
            remote = _Port({"state": "stopping"})
            owner = _LocalSupervisorOwner(
                remote,
                cast(LaunchdAdapter, _Launchd(running=False)),
                path,
                OperationalStateStore(root / "state.db"),
                ConfigStore(root / "config.toml", validate_config),
            )

            result = owner.execute("supervisor.stop", {})

        assert result == {"state": "stopping"}
        assert remote.calls == [("supervisor.stop", {})]

    def test_local_supervisor_stop_reconciles_a_stale_socket(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "mlxd.sock"
            stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            stale.bind(str(path))
            stale.close()
            remote = _Port({"state": "stopping"})
            owner = _LocalSupervisorOwner(
                remote,
                cast(LaunchdAdapter, _Launchd(running=False)),
                path,
                OperationalStateStore(root / "state.db"),
                ConfigStore(root / "config.toml", validate_config),
            )

            result = owner.execute("supervisor.stop", {})

        assert result == {"state": "stopped", "already_stopped": True}
        assert remote.calls == []

    def test_inactive_supervisor_stop_reconciles_stale_running_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = OperationalStateStore(root / "state.db")
            config = ConfigStore(root / "config.toml", validate_config)
            config.import_text(
                "schema_version = 1\n[gateway]\nhost = '127.0.0.1'\nport = 9876\n"
            )
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
                    "version": 2,
                    "state": "running",
                    "host": "127.0.0.1",
                    "port": 9876,
                }
            )
            state.put_snapshot(
                {
                    "kind": "service_run",
                    "id": "coding/run-1",
                    "version": 3,
                    "service": "coding",
                    "run_id": "run-1",
                    "state": "ready",
                    "pid": 123,
                }
            )
            state.put_operation(
                {
                    "id": "operation-1",
                    "kind": "service.start",
                    "resource": "coding",
                    "status": "running",
                }
            )
            versions = iter(range(10, 20))
            owner = _LocalSupervisorOwner(
                _Port(),
                cast(LaunchdAdapter, _Launchd(running=False)),
                root / "mlxd.sock",
                state,
                config,
                clock=lambda: next(versions),
            )

            owner.execute("supervisor.stop", {})

            supervisor = state.snapshot("supervisor", "supervisor")
            assert supervisor is not None
            assert supervisor["state"] == "stopped"
            gateway = state.snapshot("gateway", "gateway")
            assert gateway is not None
            assert gateway["state"] == "stopped"
            assert gateway["port"] == 9876
            service = state.snapshot("service_run", "coding/run-1")
            assert service is not None
            assert service["state"] == "stopped"
            assert isinstance(service, (list, tuple, Mapping, str))
            assert "pid" not in service
            operation = state.operation("operation-1")
            assert operation is not None
            assert operation["status"] == "failed"
            assert operation["outcome"] == "interrupted"
            assert state.events("operation-1")[-1]["kind"] == "interrupted"
            assert not {
                item.get("status")
                for item in state.operations()
                if item.get("status") in {"queued", "running", "resuming"}
            }

    def test_running_gateway_endpoint_edit_fails_before_preview_or_execution(
        self,
    ) -> None:
        dispatcher = _Port()
        guard = _GatewayMutationGuard(
            cast(OperationDispatcher, dispatcher),
            cast(LaunchdAdapter, _Launchd(running=True)),
        )

        with pytest.raises(ApplicationError, match="Stop the Supervisor"):
            guard.preview(OperationRequest("gateway.configure", {"port": 9000}))

        assert dispatcher.calls == []

    def test_live_control_socket_blocks_gateway_endpoint_edit(self, cleanup) -> None:
        dispatcher = _Port()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mlxd.sock"
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            cleanup.callback(listener.close)
            listener.bind(str(path))
            listener.listen(1)
            guard = _GatewayMutationGuard(
                cast(OperationDispatcher, dispatcher),
                cast(LaunchdAdapter, _Launchd(running=False)),
                path,
            )

            with pytest.raises(ApplicationError, match="Stop the Supervisor"):
                guard.execute(OperationRequest("gateway.configure", {"port": 9000}))

        assert dispatcher.calls == []

    def test_running_supervisor_allows_reconcilable_service_edit(self) -> None:
        class Dispatcher:
            def __init__(self):
                self.calls = []

            def execute(self, request):
                self.calls.append(request)
                return {"edited": True}

        dispatcher = Dispatcher()
        guard = _GatewayMutationGuard(
            cast(OperationDispatcher, dispatcher),
            cast(LaunchdAdapter, _Launchd(running=True)),
        )
        request = OperationRequest("service.edit", {"resource": "coding"})

        assert guard.execute(request) == {"edited": True}
        assert dispatcher.calls == [request]

    def test_client_sampling_defaults_cover_coding_and_memory_operations(self) -> None:
        repository = "mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit"
        revision = "70a3aa32c7feef511182bf16aa332f37e8d82014"
        coding = default_sampling(repository, revision, "codex")["coding"]
        assert coding.temperature == 0.6
        assert coding.top_p == 0.95
        assert coding.top_k == 20
        assert coding.presence_penalty == 0.0
        assert coding.enable_thinking
        hindsight = default_sampling(repository, revision, "hindsight")
        assert hindsight["verification"].temperature == 0.7
        assert hindsight["retain"].temperature == 0.7
        assert not hindsight["retain"].enable_thinking
        assert hindsight["reflect"].temperature == 1.0
        assert hindsight["reflect"].enable_thinking
        assert hindsight["consolidation"].temperature == 0.7
        assert default_sampling(repository, "0" * 40, "codex") == {}

    def test_local_status_neither_inspects_nor_activates_launchd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = MlxctlPaths(
                root / "config", root / "state", root / "data", root / "logs"
            )

            production = compose_local(
                paths=paths, home=root, executable=Path("/usr/bin/python3")
            )
            result = production.application.dispatcher.execute(
                OperationRequest("status")
            )

            assert result.value["state"] == "stopped"
            assert not (root / "Library/LaunchAgents/io.nisavid.mlxd.plist").exists()

            inspected = production.application.dispatcher.execute(
                OperationRequest("gateway.inspect")
            ).value
            credential = inspected["credential"]
            assert isinstance(credential, Mapping)
            assert credential["scheme"] == "Bearer"
            assert credential["path"] == str(paths.gateway_credential)
            assert isinstance(credential, (list, tuple, Mapping, str))
            assert set(credential) == {"scheme", "path", "instructions"}

    def test_daemon_graph_composes_without_binding_or_starting_services(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = MlxctlPaths(
                root / "config", root / "state", root / "data", root / "logs"
            )

            daemon = compose_daemon(paths=paths, home=root)

            assert isinstance(daemon, DaemonService)
            assert not paths.control_socket.exists()
            assert paths.gateway_credential.exists()
            assert stat.S_IMODE(paths.gateway_credential.stat().st_mode) == 0o600

    def test_production_graphs_reject_adoption_inside_owned_data(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "hub-cache"
            cache.mkdir()
            monkeypatch.setattr(
                "huggingface_hub.scan_cache_dir",
                partial(scan_cache_dir, cache_dir=cache),
            )
            paths = MlxctlPaths(
                root / "config", root / "state", root / "data", root / "logs"
            )
            local = compose_local(
                paths=paths, home=root, executable=Path("/usr/bin/python3")
            )
            snapshot = paths.data_dir / "external-snapshot"
            snapshot.mkdir()
            (snapshot / "weights.bin").write_bytes(b"externally owned")
            parameters = {
                "repository": "owner/model",
                "revision": "a" * 40,
                "path": str(snapshot),
            }

            with pytest.raises(Exception, match="mlxctl-owned"):
                local.application.dispatcher.preview(
                    OperationRequest("model.adopt", parameters)
                )

            daemon = compose_daemon(paths=paths, home=root)
            router = daemon._router_factory(lambda: None)
            with pytest.raises(ApplicationError, match="mlxctl-owned"):
                router.execute("model.adopt", parameters)

    def test_launchd_definition_is_inactive_and_uses_private_module_target(
        self,
    ) -> None:
        adapter = make_launchd(
            executable=Path("/usr/bin/python3"), home=Path("/Users/example")
        )

        payload = plistlib.loads(adapter.preview())

        assert not payload["KeepAlive"]
        assert not payload["RunAtLoad"]
        assert payload["ProgramArguments"] == [
            "/usr/bin/python3",
            "-m",
            "mlxctl.entrypoints",
            "daemon",
        ]
        assert (
            payload["StandardOutPath"]
            == "/Users/example/Library/Logs/mlxctl/supervisor.log"
        )
        assert payload["StandardErrorPath"] == payload["StandardOutPath"]
        assert payload["Umask"] == 0o077

    def test_local_composition_preserves_tool_environment_interpreter_symlink(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_interpreter = root / "base-python"
            base_interpreter.touch()
            tool_interpreter = root / "tool-environment-python"
            tool_interpreter.symlink_to(base_interpreter)
            paths = MlxctlPaths(
                root / "config", root / "state", root / "data", root / "logs"
            )

            production = compose_local(
                paths=paths, home=root, executable=tool_interpreter
            )
            payload = plistlib.loads(production.launchd.preview())

        assert payload["ProgramArguments"][0] == str(tool_interpreter.absolute())
        assert payload["ProgramArguments"][0] != str(base_interpreter)

    def test_runtime_installer_uses_configured_absolute_uv(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = []

        def run(*args, **kwargs):
            calls.append((args, kwargs))

        monkeypatch.setattr("mlxctl.infrastructure.production_host.subprocess.run", run)
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "uv"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o700)
            with monkeypatch.context() as patch:
                patch.setenv("MLXCTL_UV_EXECUTABLE", str(executable))
                resolved = resolve_uv(Path(directory))
            AbsoluteUvRunner(resolved).run(("uv", "--version"))

        assert calls[-1][0][0] == (str(executable.resolve()), "--version")
        assert not calls[-1][1]["shell"]

    def test_router_dispatches_all_physical_owner_families(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = OperationalStateStore(Path(directory) / "state.sqlite3")
            runtime = _Port({"installation_id": "runtime"})
            model = _Port({"installation_id": "model"})
            supervisor = _Port({"state": "stopped"})
            stops = []
            router = DaemonOperationRouter(
                runtime=runtime,
                model=model,
                supervisor=supervisor,
                state=state,
                request_stop=lambda: stops.append(True),
            )

            router.execute("runtime.install", {"runtime": "optiq"})
            router.execute("model.install", {"repository": "owner/model"})
            router.execute("service.drain", {"resource": "coding"})
            router.execute("supervisor.stop", {})

            assert runtime.calls[0][0] == "runtime.install"
            assert model.calls[0][0] == "model.install"
            assert supervisor.calls[0][0] == "service.drain"
            assert stops
            assert state.operations()[0]["status"] == "complete"
            supervisor = state.snapshot("supervisor", "supervisor")
            assert supervisor is not None
            assert supervisor["state"] == "stopped"
            gateway = state.snapshot("gateway", "gateway")
            assert gateway is not None
            assert gateway["port"] == 8766
            assert {metric["scope"] for metric in state.metrics()} == {
                "gateway",
                "supervisor",
            }

    def test_router_rejects_unowned_resume_instead_of_faking_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            router = DaemonOperationRouter(
                runtime=_Port(),
                model=_Port(),
                supervisor=_Port(),
                state=OperationalStateStore(Path(directory) / "state.sqlite3"),
            )

            with pytest.raises(ApplicationError, match="not owned"):
                router.execute("operation.resume", {"resource": "unknown"})

    def test_supervisor_stop_drains_physical_work_and_rejects_new_work(self) -> None:
        class BlockingPort(_Port):
            def __init__(self) -> None:
                super().__init__({"state": "complete"})
                self.entered = threading.Event()
                self.release = threading.Event()

            def execute(self, operation, parameters):
                self.entered.set()
                self.release.wait(1)
                return super().execute(operation, parameters)

        with tempfile.TemporaryDirectory() as directory:
            runtime = BlockingPort()
            supervisor = _Port({"state": "stopped"})
            router = DaemonOperationRouter(
                runtime=runtime,
                model=_Port(),
                supervisor=supervisor,
                state=OperationalStateStore(Path(directory) / "state.sqlite3"),
                physical_drain_timeout=1,
            )
            physical = threading.Thread(
                target=lambda: router.execute("runtime.install", {"runtime": "optiq"})
            )
            physical.start()
            assert runtime.entered.wait(1)
            stopped = []
            stopping = threading.Thread(
                target=lambda: stopped.append(router.execute("supervisor.stop", {}))
            )
            stopping.start()
            time.sleep(0.02)

            with pytest.raises(ApplicationError) as raised:
                router.execute("model.install", {"repository": "owner/model"})
            assert raised.value.code == "supervisor_stopping"
            assert supervisor.calls == []

            runtime.release.set()
            physical.join(1)
            stopping.join(1)
            assert stopped[0]["state"] == "stopped"
            assert supervisor.calls == [("supervisor.stop", {})]

    def test_state_removal_rejects_symlink_even_when_it_resolves_to_owned_path(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            owned = root / "owned"
            owned.mkdir()
            link = root / "link"
            link.symlink_to(owned, target_is_directory=True)
            remover = OwnedStateRemover((owned,))

            with pytest.raises(ApplicationError, match="outside mlxctl ownership"):
                remover.execute(
                    "state.remove", {"paths": [str(link)], "confirmed": True}
                )
            assert owned.exists()

    def test_recommended_setup_blocks_undersized_mac(self) -> None:
        with pytest.raises(ValueError, match="no recommended setup profile fits"):
            _setup_planner().plan(
                SetupPreflight(
                    "darwin",
                    "arm64",
                    memory_bytes=16 * 1024**3,
                    disk_free_bytes=100 * 1024**3,
                    online=True,
                )
            )

    def test_recommended_setup_defaults_to_balanced_capacity_and_clients(self) -> None:
        preview = _setup_planner().preview(
            _setup_planner().plan(
                SetupPreflight(
                    "darwin",
                    "arm64",
                    memory_bytes=48 * 1024**3,
                    disk_free_bytes=100 * 1024**3,
                    online=True,
                )
            )
        )

        assert preview.capacity_profile == "balanced"
        assert preview.context_window == 131_072
        assert preview.service_options["max_context"] == 131_072
        assert preview.service_options["max_concurrent"] == 6
        assert preview.service_options["prompt_cache_bytes"] == 2 * 1024**3
        assert "temperature" not in preview.service_options
        assert preview.projected_kv_bytes == 5_737_807_872
        assert preview.clients == ("codex", "hindsight")
        assert isinstance(preview.client_options["codex"]["sampling_profiles"], Mapping)
        assert (
            preview.client_options["codex"]["sampling_profiles"]["coding"][
                "temperature"
            ]
            == 0.6
        )
        assert (
            preview.client_options["codex"]["sampling_profiles"]["coding"]["top_k"]
            == 20
        )
        assert preview.client_options["codex"]["sampling_profiles"]["coding"][
            "enable_thinking"
        ]
        assert (
            preview.client_options["codex"]["sampling_profiles"]["coding"][
                "upstream_profile"
            ]
            == "precise-coding-thinking"
        )
        assert (
            preview.client_options["codex"]["sampling_profiles"]["coding"][
                "source_revision"
            ]
            == "995ad96eacd98c81ed38be0c5b274b04031597b0"
        )
        assert isinstance(
            preview.client_options["hindsight"]["sampling_profiles"], Mapping
        )
        assert (
            preview.client_options["hindsight"]["sampling_profiles"]["retain"][
                "temperature"
            ]
            == 0.7
        )
        assert not preview.client_options["hindsight"]["sampling_profiles"]["retain"][
            "enable_thinking"
        ]
        assert (
            preview.client_options["hindsight"]["sampling_profiles"]["reflect"][
                "temperature"
            ]
            == 1.0
        )
        assert preview.client_options["hindsight"]["max_concurrent"] == 1

    def test_gateway_requests_append_to_openai_v1_base_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = []

        def post(url, **kwargs):
            calls.append((url, kwargs))
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"choices": [{"message": {"content": "mlxctl ready"}}]},
            )

        monkeypatch.setattr("mlxctl.infrastructure.production_host.httpx.post", post)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credential = GatewayCredential(root / "gateway.token")
            token = credential.load_or_create()
            result = GatewayVerificationPort(credential).execute(
                "verify.request",
                {
                    "endpoint": "http://127.0.0.1:8766/v1",
                    "model": "coding",
                    "request": "Respond with exactly: mlxctl ready",
                },
            )
            client_request(
                "http://127.0.0.1:8766/v1",
                "coding",
                {"messages": []},
                credential=credential,
            )

        assert result["text"] == "mlxctl ready"
        assert [url for url, _ in calls] == [
            "http://127.0.0.1:8766/v1/chat/completions",
            "http://127.0.0.1:8766/v1/chat/completions",
        ]
        assert [kwargs["headers"]["authorization"] for _, kwargs in calls] == [
            f"Bearer {token}",
            f"Bearer {token}",
        ]
        assert calls[1][1]["json"]["messages"][0]["role"] == "user"

    def test_launch_supply_keeps_config_key_but_uses_exact_revision_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            revision = "a" * 40
            snapshot = Path(directory) / "snapshot"
            snapshot.mkdir()
            config = validate_config(
                {
                    "schema_version": 1,
                    "models": {
                        "friendly-installation": {
                            "repository": "owner/model",
                            "revision": revision,
                        }
                    },
                }
            )
            inventory = CacheInventory(
                (
                    CachedRevision(
                        f"owner/model@{revision}",
                        "owner/model",
                        revision,
                        snapshot,
                        0,
                        "local-observed",
                        True,
                    ),
                ),
                "local-observed",
                (),
            )

            installations = configured_model_installations(config, inventory)

            assert set(installations) == {"friendly-installation"}
            assert (
                installations["friendly-installation"].installation_id
                == f"owner/model@{revision}"
            )

    def test_launch_supply_uses_adopted_external_snapshot_without_cache_entry(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            revision = "a" * 40
            snapshot = Path(directory) / "external"
            snapshot.mkdir()
            config = validate_config(
                {
                    "schema_version": 1,
                    "models": {
                        "adopted": {
                            "repository": "owner/model",
                            "revision": revision,
                            "provenance": "adopted",
                            "path": str(snapshot),
                        }
                    },
                }
            )
            inventory = CacheInventory((), "local-observed", ())

            installations = configured_model_installations(config, inventory)

            assert installations["adopted"].snapshot_path == snapshot
            assert installations["adopted"].provenance.source == "external-adopted"
            assert installations["adopted"].installation_id == f"owner/model@{revision}"


class _FakeRouter(_Port):
    def __init__(self, request_stop) -> None:
        super().__init__({"state": "stopped"})
        self.request_stop = request_stop
        self.start_calls = 0
        self.stop_calls = 0

    def start(self):
        self.start_calls += 1
        return {"state": "running"}

    def stop(self):
        self.stop_calls += 1
        return {"state": "stopped"}

    def cancel(self, operation_id):
        return False

    def maintain(self):
        self.calls.append(("maintain", {}))
        return {"state": "running"}

    def record_maintenance_failure(self, error):
        self.calls.append(("maintenance_failure", type(error).__name__))

    def execute(self, operation, parameters, *, operation_id=None):
        value = super().execute(operation, parameters)
        if operation == "supervisor.stop":
            self.request_stop()
        return value


class _FakeServer:
    def __init__(self, _path, handler, *, cancel_handler) -> None:
        self.handler = handler
        self.cancel_handler = cancel_handler
        self.progress = []
        self.closed = False
        self.request_task = None

    async def start(self):
        from mlxctl.infrastructure.control_protocol import ControlRequest

        async def emit(value):
            self.progress.append(dict(value))

        self.request_task = asyncio.create_task(
            self.handler(
                ControlRequest("request", "operation", "supervisor.stop", {}), emit
            )
        )

    async def close(self):
        if self.request_task is not None:
            await self.request_task
        self.closed = True


@pytest.mark.asyncio(loop_scope="function")
class TestDaemonService:
    async def test_explicit_supervisor_stop_closes_control_service(self) -> None:
        routers = []
        servers = []

        def router_factory(request_stop):
            router = _FakeRouter(request_stop)
            routers.append(router)
            return cast(DaemonOperationRouter, router)

        def server_factory(*args, **kwargs):
            server = _FakeServer(*args, **kwargs)
            servers.append(server)
            return cast(UnixControlServer, server)

        service = DaemonService(
            Path("/tmp/mlxd-test.sock"),
            router_factory,
            server_factory=server_factory,
        )

        await asyncio.wait_for(service.serve(), timeout=1)

        assert routers[0].start_calls == 1
        assert routers[0].stop_calls == 1
        assert [item["phase"] for item in servers[0].progress] == [
            "started",
            "complete",
        ]
        assert servers[0].closed

    async def test_daemon_runs_periodic_maintenance_without_cli_requests(self) -> None:
        routers = []

        class IdleServer:
            async def start(self):
                return None

            async def close(self):
                return None

        def router_factory(request_stop):
            router = _FakeRouter(request_stop)
            routers.append(router)
            return cast(DaemonOperationRouter, router)

        service = DaemonService(
            Path("/tmp/mlxd-maintenance-test.sock"),
            router_factory,
            server_factory=lambda *args, **kwargs: cast(
                UnixControlServer, IdleServer()
            ),
            maintenance_interval=0.01,
        )
        task = asyncio.create_task(service.serve())
        await asyncio.sleep(0.04)
        routers[0].request_stop()
        await asyncio.wait_for(task, timeout=1)

        assert sum(call[0] == "maintain" for call in routers[0].calls) >= 2
