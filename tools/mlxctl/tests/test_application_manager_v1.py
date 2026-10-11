import pytest

from mlxctl.application.catalogue import build_operation_catalogue
from mlxctl.application.dispatch import (
    ApplicationError,
    OperationDispatcher,
    OperationRequest,
)
from mlxctl.application.manager import ApplicationManager, PreparedOperation


class _Activator:
    def __init__(self) -> None:
        self.calls = 0

    def activate(self) -> None:
        self.calls += 1


class _Backend:
    def __init__(self) -> None:
        self.prepared = []
        self.require = set()

    def prepare(self, request: OperationRequest) -> PreparedOperation:
        self.prepared.append(request)
        return PreparedOperation(
            requires_supervisor=request.name in self.require,
            execute=lambda: {
                "operation": request.name,
                "parameters": dict(request.parameters),
            },
            events=({"phase": "plan", "state": "complete"},),
        )


class TestApplicationManager:
    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.catalogue = build_operation_catalogue()
        self.activator = _Activator()
        self.dispatcher = OperationDispatcher(self.catalogue, self.activator)
        self.backend = _Backend()
        ApplicationManager(self.catalogue, self.backend).register(self.dispatcher)

    def test_registers_every_cli_and_tui_operation(
        self, subtests: pytest.Subtests
    ) -> None:
        for name, operation in self.catalogue.items():
            with subtests.test(operation=name):
                parameters = {"confirmed": True} if operation.confirmation else {}
                result = self.dispatcher.execute(OperationRequest(name, parameters))
                assert result.operation == name

    def test_confirmation_is_enforced_below_both_interfaces(self) -> None:
        with pytest.raises(ApplicationError) as raised:
            self.dispatcher.execute(
                OperationRequest("model.cache.evict", {"resource": "cached"})
            )

        assert raised.value.code == "confirmation_required"

    def test_preview_resolves_the_backend_plan_without_execution_or_activation(
        self,
    ) -> None:
        self.backend.require.add("model.cache.evict")

        result = self.dispatcher.preview(
            OperationRequest("model.cache.evict", {"resource": "cached"})
        )

        assert result.value["state"] == "planned"
        assert result.value["confirmation_required"]
        assert result.value["requires_supervisor"]
        assert isinstance(result.value["plan"], (list, tuple))
        assert result.value["plan"][0]["phase"] == "plan"
        assert self.activator.calls == 0

    def test_preview_promotes_exact_plan_identity_for_interface_confirmation(self):
        original = self.backend.prepare

        def prepare(request):
            prepared = original(request)
            return PreparedOperation(
                prepared.requires_supervisor,
                prepared.execute,
                ({"phase": "plan", "plan_fingerprint": "sha256:exact"},),
            )

        self.backend.prepare = prepare

        result = self.dispatcher.preview(OperationRequest("setup"))

        assert result.value["plan_fingerprint"] == "sha256:exact"

    def test_service_start_can_visibly_activate_supervisor(self) -> None:
        self.backend.require.add("service.start")

        result = self.dispatcher.execute(
            OperationRequest("service.start", {"resource": "coding"})
        )

        assert result.supervisor_started
        assert self.activator.calls == 1

    def test_local_config_mutation_does_not_start_supervisor(self) -> None:
        result = self.dispatcher.execute(
            OperationRequest("config.restore", {"confirmed": True})
        )

        assert not result.supervisor_started
        assert self.activator.calls == 0

    def test_supervisor_stop_uses_a_running_supervisor_without_starting_one(
        self,
    ) -> None:
        self.backend.require.add("supervisor.stop")

        result = self.dispatcher.execute(
            OperationRequest("supervisor.stop", {"confirmed": True})
        )

        assert not result.supervisor_started
        assert self.activator.calls == 0
        assert result.value["operation"] == "supervisor.stop"

    def test_backend_cannot_activate_a_read_only_operation(self) -> None:
        self.backend.require.add("status")

        with pytest.raises(ApplicationError) as raised:
            self.dispatcher.execute(OperationRequest("status"))

        assert raised.value.code == "activation_forbidden"
        assert self.activator.calls == 0

    def test_backend_result_and_progress_are_normalized(self) -> None:
        result = self.dispatcher.execute(OperationRequest("runtime.available"))

        assert result.value["operation"] == "runtime.available"
        assert result.events[0]["phase"] == "plan"
