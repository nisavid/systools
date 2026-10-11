from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import TypeVar, cast

import pytest

from mlxctl.application.config_schema import ClientSamplingSettings, ClientSettings
from mlxctl.application.dispatch import ApplicationError
from mlxctl.infrastructure.client_integrations import (
    ClientApplyResult,
    ClientConfiguration,
    ClientRemovalResult,
    SamplingProfile,
    SemanticChange,
    TestRequest,
)
from mlxctl.infrastructure.control_client import (
    ControlResponse,
    SupervisorUnavailableError,
)
from mlxctl.infrastructure.operation_ports import (
    ClientOperationPort,
    RemoteOperationPort,
    SupervisorOperationPort,
)
from mlxctl.infrastructure.supervisor_v1 import Supervisor

Result = TypeVar("Result")


class FakeControlClient:
    def __init__(self) -> None:
        self.calls = []
        self.error = None

    def execute(self, operation, parameters=None):
        self.calls.append(("execute", operation, dict(parameters or {})))
        if self.error:
            raise self.error
        return ControlResponse(
            request_id="request-1",
            result={"state": "ready"},
            operation_id="op-1",
            progress=({"phase": "start"},),
        )

    def cancel(self, operation_id):
        self.calls.append(("cancel", operation_id))
        return ControlResponse(
            request_id="request-1",
            result={"cancelled": True},
            operation_id=operation_id,
            progress=(),
        )


class FakeSupervisor:
    def __init__(self) -> None:
        self.calls = []

    def start(self):
        self.calls.append(("start",))
        return {"state": "running"}

    def stop(self):
        self.calls.append(("stop",))
        return {"state": "stopped"}

    def restart(self):
        self.calls.append(("restart",))
        return {"state": "running"}

    def start_service(self, resource):
        self.calls.append(("start_service", resource))
        return {"service": resource, "state": "ready"}

    def drain_service(self, resource):
        self.calls.append(("drain_service", resource))
        return {"service": resource, "state": "drained"}


class FakeClientAdapter:
    def __init__(self) -> None:
        self.calls = []

    def preview(self, configuration):
        self.calls.append(("preview", configuration.service_name))
        return (SemanticChange(("model",), None, configuration.service_name),)

    def apply(self, configuration, *, takeover=False):
        self.calls.append(("apply", configuration.service_name, takeover))
        return ClientApplyResult(True, (), Path("backup"), Path("manifest"))

    def remove(self) -> ClientRemovalResult:
        self.calls.append(("remove",))
        return ClientRemovalResult(True, ())

    def test(
        self,
        configuration: ClientConfiguration,
        request: TestRequest[Result],
        *,
        profile: str,
    ) -> Result:
        self.calls.append(("test", profile))
        return request(
            configuration.gateway_endpoint,
            configuration.service_name,
            configuration.sampling_profiles[profile].values(),
        )

    def stop_service(self, resource):
        self.calls.append(("stop_service", resource))
        return {"service": resource, "state": "stopped"}

    def restart_service(self, resource):
        self.calls.append(("restart_service", resource))
        return {"service": resource, "state": "ready"}


class TestOperationPort:
    def test_remote_port_preserves_progress_and_cancel_identity(self) -> None:
        client = FakeControlClient()
        port = RemoteOperationPort(client)

        result = port.execute("service.start", {"resource": "coding"})
        cancelled = port.execute("operation.cancel", {"resource": "op-7"})

        assert result["operation_id"] == "op-1"
        assert result["control_operation_id"] == "op-1"
        assert result["progress"] == [{"phase": "start"}]
        assert cancelled["operation_id"] == "op-7"
        assert ("cancel", "op-7") in client.calls

    def test_remote_port_preserves_owner_durable_operation_identity(self) -> None:
        client = FakeControlClient()
        original = client.execute

        def execute(operation, parameters=None):
            response = original(operation, parameters)
            return replace(
                response, result={"operation_id": "durable-op-9", "state": "ready"}
            )

        client.execute = execute
        result = RemoteOperationPort(client).execute("service.start", {})

        assert result["operation_id"] == "durable-op-9"
        assert result["control_operation_id"] == "op-1"

    def test_remote_errors_are_stable_application_errors(self) -> None:
        client = FakeControlClient()
        client.error = SupervisorUnavailableError(
            "supervisor_unavailable", "not running"
        )

        with pytest.raises(ApplicationError) as raised:
            RemoteOperationPort(client).execute("service.start", {"resource": "coding"})

        assert raised.value.code == "supervisor_unavailable"

    def test_direct_port_maps_named_lifecycle_without_ambiguity(self) -> None:
        supervisor = FakeSupervisor()
        port = SupervisorOperationPort(cast(Supervisor, supervisor))

        started = port.execute("service.start", {"resource": "coding"})
        drained = port.execute("service.drain", {"resource": "coding"})
        stopped = port.execute("supervisor.stop", {})

        assert started == {"service": "coding", "state": "ready"}
        assert drained == {"service": "coding", "state": "drained"}
        assert stopped["state"] == "stopped"
        assert supervisor.calls == [
            ("start_service", "coding"),
            ("drain_service", "coding"),
            ("stop",),
        ]

    def test_client_port_uses_one_preview_apply_test_remove_contract(self) -> None:
        adapter = FakeClientAdapter()
        records = []
        persisted = {}

        def configuration(name, parameters, settings):
            return ClientConfiguration(
                "http://127.0.0.1:8766/v1",
                "coding",
                sampling_profiles={"coding": SamplingProfile(temperature=0.0)},
                service_identity="coding-internal",
            )

        port = ClientOperationPort(
            lambda operation, name, parameters, settings: adapter,
            configuration,
            request=lambda endpoint, model, sampling: {"model": model, **sampling},
            settings=lambda name: persisted.get(name),
            record=lambda name, value: (
                records.append((name, value)),
                persisted.pop(name, None)
                if value is None
                else persisted.__setitem__(name, value),
            ),
        )

        configured = port.execute(
            "client.configure", {"client": "codex", "service": "coding"}
        )
        tested = port.execute("client.test", {"resource": "codex"})
        removed = port.execute("client.remove", {"resource": "codex"})

        assert isinstance(configured["result"], Mapping)
        assert configured["result"]["changed"]
        recorded = records[0][1]
        assert recorded is not None
        assert recorded.service == "coding-internal"
        assert isinstance(tested["response"], Mapping)
        assert tested["response"]["model"] == "coding"
        assert removed["changed"]
        assert [call[0] for call in adapter.calls] == [
            "preview",
            "apply",
            "test",
            "remove",
        ]
        assert records[-1] == ("codex", None)

    def test_hindsight_profile_is_required_then_persisted_for_test_and_remove(
        self,
    ) -> None:
        adapter = FakeClientAdapter()
        records = {}
        factory_calls = []

        def adapter_factory(operation, name, parameters, settings):
            factory_calls.append((operation, name, dict(parameters), settings))
            return adapter

        def configuration(name, parameters, settings):
            service = settings.service if settings else str(parameters["service"])
            return ClientConfiguration(
                "http://127.0.0.1:8766/v1",
                service,
                context_window=32768,
                sampling_profiles={
                    "verification": SamplingProfile(temperature=0.0),
                    "retain": SamplingProfile(temperature=0.1),
                    "reflect": SamplingProfile(temperature=0.9),
                    "consolidation": SamplingProfile(temperature=0.0),
                },
            )

        port = ClientOperationPort(
            adapter_factory,
            configuration,
            request=lambda endpoint, model, sampling: {"model": model, **sampling},
            settings=lambda name: records.get(name),
            record=lambda name, value: (
                records.pop(name, None)
                if value is None
                else records.__setitem__(name, value)
            ),
        )

        with pytest.raises(ApplicationError, match="profile"):
            port.execute(
                "client.configure", {"client": "hindsight", "service": "memory"}
            )

        port.execute(
            "client.configure",
            {
                "client": "hindsight",
                "service": "memory",
                "profile": "agent-memory",
            },
        )
        stored = records["hindsight"]
        assert isinstance(stored, ClientSettings)
        assert stored.profile == "agent-memory"
        assert stored.context_window == 32768
        assert stored.sampling["reflect"] == ClientSamplingSettings(temperature=0.9)

        port.execute("client.test", {"resource": "hindsight", "profile": "retain"})
        port.execute("client.remove", {"resource": "hindsight"})

        assert factory_calls[1][3].profile == "agent-memory"
        assert factory_calls[2][3].profile == "agent-memory"
        assert "hindsight" not in records

    def test_extra_client_profiles_fail_before_external_apply(self) -> None:
        adapter = FakeClientAdapter()
        port = ClientOperationPort(
            lambda operation, name, parameters, settings: adapter,
            lambda name, parameters, settings: ClientConfiguration(
                "http://127.0.0.1:8766/v1",
                "coding",
                sampling_profiles={
                    "coding": SamplingProfile(temperature=0.6),
                    "surprise": SamplingProfile(temperature=0.6),
                },
            ),
            request=lambda endpoint, model, sampling: {},
        )

        with pytest.raises(ApplicationError, match="requires sampling profiles"):
            port.execute("client.configure", {"client": "codex", "service": "coding"})

        assert adapter.calls == []

    def test_hindsight_profile_cannot_change_without_precise_removal(self) -> None:
        stored = ClientSettings(
            name="hindsight",
            kind="hindsight",
            service="memory",
            profile="first",
            context_window=None,
            provider="openai",
            max_concurrent=1,
            sampling={},
        )
        port = ClientOperationPort(
            lambda operation, name, parameters, settings: FakeClientAdapter(),
            lambda name, parameters, settings: ClientConfiguration(
                "http://127.0.0.1:8766/v1", "memory"
            ),
            request=lambda endpoint, model, sampling: {},
            settings=lambda name: stored,
        )

        with pytest.raises(ApplicationError, match="[Rr]emove"):
            port.execute(
                "client.configure",
                {
                    "client": "hindsight",
                    "service": "memory",
                    "profile": "second",
                },
            )

    def test_partial_precise_removal_retains_desired_state_identity(self) -> None:
        stored = ClientSettings(
            name="codex",
            kind="codex",
            service="coding",
            profile=None,
            context_window=32768,
            provider="mlx-local",
            max_concurrent=None,
            sampling={},
        )
        adapter = FakeClientAdapter()
        adapter.remove = lambda: ClientRemovalResult(
            changed=True,
            changes=(),
            skipped_paths=(("model",),),
        )
        recorded = []
        port = ClientOperationPort(
            lambda operation, name, parameters, settings: adapter,
            lambda name, parameters, settings: ClientConfiguration(
                "http://127.0.0.1:8766/v1", "coding"
            ),
            request=lambda endpoint, model, sampling: {},
            settings=lambda name: stored,
            record=lambda name, value: recorded.append((name, value)),
        )

        result = port.execute("client.remove", {"resource": "codex"})

        assert result["desired_state_retained"]
        assert result["skipped_paths"] == [["model"]]
        assert recorded == []
