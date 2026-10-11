from __future__ import annotations

from dataclasses import dataclass

import pytest

from mlxctl.domain.admission import PressureLevel
from mlxctl.domain.resources import (
    ActivationPolicy,
    InferenceService,
    ResourceName,
    ServiceRunState,
)
from mlxctl.infrastructure.supervisor_v1 import (
    CapabilityValidationError,
    ManagedProcess,
    PreparedLaunch,
    ProcessIdentity,
    Supervisor,
)


def _service(
    name: str,
    *,
    pinned: bool = False,
    route: str | None = None,
    activation: ActivationPolicy = ActivationPolicy.MANUAL,
) -> InferenceService:
    return InferenceService(
        name=ResourceName(name),
        model_alias=ResourceName(f"{name}-model"),
        runtime_installation="optiq@0.2.18",
        route=ResourceName(route or name),
        activation=activation,
        pinned=pinned,
        options={"context_length": 32768},
    )


class FakeDesiredState:
    def __init__(self, *services: InferenceService) -> None:
        self.items = {str(service.name): service for service in services}

    def service(self, name: str) -> InferenceService | None:
        return self.items.get(name)

    def services(self) -> tuple[InferenceService, ...]:
        return tuple(self.items.values())


class FakeRuntimeSupply:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []
        self.error: Exception | None = None
        self.revision = "v1"

    def prepare_launch(
        self, service: InferenceService, host: str, port: int
    ) -> PreparedLaunch:
        self.calls.append((str(service.name), host, port))
        if self.error:
            raise self.error
        return PreparedLaunch(
            argv=("/runtime/bin/server", "--port", str(port)),
            environment={
                "MODEL_ALIAS": str(service.model_alias),
                "REVISION": self.revision,
            },
            required_capabilities=frozenset({"model", "host", "port"}),
            observed_capabilities=frozenset(
                {"model", "host", "port", "context_length"}
            ),
        )


class FakeStateStore:
    def __init__(self) -> None:
        self.operation_items: dict[str, dict[str, object]] = {}
        self.event_items: list[dict[str, object]] = []
        self.snapshot_items: list[dict[str, object]] = []

    def put_operation(self, operation):
        self.operation_items[str(operation["id"])] = dict(operation)
        return operation

    def append_event(self, event):
        self.event_items.append(dict(event))
        return {**event, "sequence": len(self.event_items)}

    def put_snapshot(self, snapshot):
        self.snapshot_items.append(dict(snapshot))
        return snapshot

    def snapshots(self, kind=None):
        return tuple(
            item for item in self.snapshot_items if kind is None or item["kind"] == kind
        )


@dataclass
class FakeProcess:
    pid: int
    running: bool = True
    ignores_terminate: bool = False
    terminate_calls: int = 0
    kill_calls: int = 0

    def poll(self):
        return None if self.running else 0

    def terminate(self):
        self.terminate_calls += 1
        if not self.ignores_terminate:
            self.running = False

    def kill(self):
        self.kill_calls += 1
        self.running = False

    def wait(self, timeout):
        if self.running:
            raise TimeoutError
        return 0


class FakeProcesses:
    def __init__(self) -> None:
        self.next_port = 49152
        self.launched: list[tuple[tuple[str, ...], dict[str, str]]] = []
        self.processes: dict[int, FakeProcess] = {}
        self.attached: list[int] = []

    def allocate_loopback_port(self, host: str) -> int:
        port = self.next_port
        self.next_port += 1
        return port

    def launch(self, argv, environment):
        process = FakeProcess(1000 + len(self.processes))
        self.processes[process.pid] = process
        self.launched.append((tuple(argv), dict(environment)))
        return process

    def attach(self, pid: int):
        self.attached.append(pid)
        return self.processes.get(pid)


class FakeProbe:
    def __init__(self) -> None:
        self.ready = True
        self.identities: dict[int, ProcessIdentity] = {}

    def identity(self, process: ManagedProcess) -> ProcessIdentity:
        identity = ProcessIdentity(process.pid, f"birth-{process.pid}")
        self.identities[process.pid] = identity
        return identity

    def identity_matches(self, identity: ProcessIdentity) -> bool:
        return self.identities.get(identity.pid) == identity

    def is_ready(self, endpoint: str, timeout: float) -> bool:
        return self.ready


class FakeGateway:
    def __init__(self) -> None:
        self.running = False
        self.shedding = False
        self.routes: dict[str, tuple[str, str | None]] = {}
        self.calls: list[object] = []
        self.busy_services: set[str] = set()
        self.last_used: dict[str, int] = {}
        self.descriptions = {}

    def start(self):
        self.running = True
        self.calls.append("start")

    def set_route(self, service: str, state: str, endpoint: str | None):
        self.routes[service] = (state, endpoint)
        self.calls.append(("route", service, state, endpoint))

    def describe_route(self, route):
        self.descriptions[route.service] = route

    def remove_route(self, service: str):
        self.routes.pop(service, None)
        self.descriptions.pop(service, None)
        self.calls.append(("remove_route", service))

    def shed_new_work(self, enabled: bool):
        self.shedding = enabled
        self.calls.append(("shed", enabled))

    def effective_route(self, service: str) -> tuple[str, str | None]:
        state, endpoint = self.routes[service]
        if self.shedding and state == "ready":
            return ("unavailable", None)
        return (state, endpoint)

    def is_busy(self, service: str) -> bool:
        return service in self.busy_services

    def last_used_ns(self, service: str) -> int:
        return self.last_used.get(service, 0)

    def drain(self, timeout: float):
        self.calls.append(("drain", timeout))

    def stop(self, timeout: float):
        self.running = False
        self.calls.append(("stop", timeout))


class FakePressure:
    def __init__(self) -> None:
        self.level = PressureLevel.NORMAL

    def current(self) -> PressureLevel:
        return self.level


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000

    def monotonic(self) -> float:
        self.now += 1
        return float(self.now)

    def time_ns(self) -> int:
        self.now += 1
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(1, int(seconds))


class TestSupervisor:
    def test_route_listing_omits_a_service_removed_during_resolution(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        services = self.desired.services

        def remove_after_enumeration() -> tuple[InferenceService, ...]:
            enumerated = services()
            self.desired.items.pop("coding", None)
            return enumerated

        monkeypatch.setattr(self.desired, "services", remove_after_enumeration)

        routes = self.supervisor.list_routes()

        assert tuple(route.service for route in routes) == ("memory",)
        assert self.processes.launched == []

    def test_supervisor_activation_policy_starts_only_selected_services(self) -> None:
        self.desired = FakeDesiredState(
            _service("manual"),
            _service("automatic", activation=ActivationPolicy.SUPERVISOR),
        )
        supervisor = Supervisor(
            desired_state=self.desired,
            runtime_supply=self.runtime,
            state_store=self.store,
            gateway=self.gateway,
            processes=self.processes,
            probe=self.probe,
            memory_pressure=self.pressure,
            clock=self.clock,
            readiness_timeout=3,
            drain_timeout=2,
            terminate_timeout=1,
        )

        status = supervisor.start()

        assert {
            run.service for run in status.runs if run.state is ServiceRunState.READY
        } == {"automatic"}
        assert self.gateway.routes["manual"][0] == "stopped"

    def test_public_gateway_route_is_distinct_from_service_resource_name(self) -> None:
        service = _service("worker", route="coding")
        self.desired = FakeDesiredState(service)
        supervisor = Supervisor(
            desired_state=self.desired,
            runtime_supply=self.runtime,
            state_store=self.store,
            gateway=self.gateway,
            processes=self.processes,
            probe=self.probe,
            memory_pressure=self.pressure,
            clock=self.clock,
            readiness_timeout=3,
            drain_timeout=2,
            terminate_timeout=1,
        )

        supervisor.start()
        transition = supervisor.start_service("worker")

        assert transition.run.state == ServiceRunState.READY
        assert "coding" in self.gateway.routes
        assert "worker" not in self.gateway.routes
        route = supervisor.resolve("coding")
        assert route is not None
        assert route.service == "coding"
        assert route.model == "worker-model"
        assert route.runtime == "optiq@0.2.18"

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.desired = FakeDesiredState(_service("coding"), _service("memory"))
        self.runtime = FakeRuntimeSupply()
        self.store = FakeStateStore()
        self.gateway = FakeGateway()
        self.processes = FakeProcesses()
        self.probe = FakeProbe()
        self.pressure = FakePressure()
        self.clock = FakeClock()
        self.supervisor = Supervisor(
            desired_state=self.desired,
            runtime_supply=self.runtime,
            state_store=self.store,
            gateway=self.gateway,
            processes=self.processes,
            probe=self.probe,
            memory_pressure=self.pressure,
            clock=self.clock,
            readiness_timeout=3,
            drain_timeout=2,
            terminate_timeout=1,
        )

    def test_status_is_read_only_and_explicit_start_owns_one_gateway(self) -> None:
        assert self.supervisor.status().state == "stopped"
        assert self.gateway.calls == []

        started = self.supervisor.start()
        again = self.supervisor.start()

        assert started.state == "running"
        assert again.state == "running"
        assert self.gateway.calls.count("start") == 1
        assert self.gateway.routes == {
            "coding": ("stopped", None),
            "memory": ("stopped", None),
        }
        operation = next(iter(self.store.operation_items.values()))
        assert operation["status"] == "complete"
        assert operation["outcome"] == "running"

    def test_service_start_visibly_activates_and_runs_multiple_named_services(self):
        coding = self.supervisor.start_service("coding")
        memory = self.supervisor.start_service("memory")

        assert coding.supervisor_started
        assert not memory.supervisor_started
        assert coding.run.state == ServiceRunState.READY
        assert coding.run.run_id != memory.run.run_id
        assert coding.run.upstream_port != memory.run.upstream_port
        assert len(self.processes.launched) == 2
        assert self.gateway.routes["coding"][0] == "ready"
        assert self.gateway.routes["memory"][0] == "ready"

    def test_capabilities_are_validated_before_process_launch(self) -> None:
        self.runtime.error = CapabilityValidationError("mtp is unavailable")

        result = self.supervisor.start_service("coding")

        assert result.run.state == ServiceRunState.REJECTED
        assert "mtp is unavailable" in (result.run.error or "")
        assert self.processes.launched == []

        with pytest.raises(CapabilityValidationError, match="exact capabilities: mtp"):
            PreparedLaunch(
                argv=("/runtime/bin/server",),
                required_capabilities=frozenset({"mtp"}),
                observed_capabilities=frozenset({"model"}),
            )

    def test_readiness_timeout_forces_bounded_cleanup(self) -> None:
        self.probe.ready = False

        result = self.supervisor.start_service("coding")

        process = next(iter(self.processes.processes.values()))
        assert result.run.state == ServiceRunState.FAILED
        assert process.terminate_calls == 1
        assert not process.running

    def test_stop_restart_and_supervisor_shutdown_are_bounded_and_journaled(self):
        first = self.supervisor.start_service("coding")
        assert first.run.pid is not None
        self.processes.processes[first.run.pid].ignores_terminate = True

        restarted = self.supervisor.restart_service("coding")
        stopped = self.supervisor.stop()

        assert first.run.run_id != restarted.run.run_id
        assert first.run.pid is not None
        assert self.processes.processes[first.run.pid].kill_calls == 1
        assert stopped.state == "stopped"
        assert not self.gateway.running
        assert ("drain", 2) in self.gateway.calls

    def test_supervisor_restart_restores_gateway_admission_for_ready_service(self):
        self.supervisor.start_service("coding")

        self.supervisor.restart()
        restarted = self.supervisor.start_service("coding")

        assert restarted.run.state == ServiceRunState.READY
        assert self.gateway.effective_route("coding") == (
            "ready",
            "http://127.0.0.1:49153",
        )

    def test_service_drain_rejects_new_route_work_and_waits_for_idle(self) -> None:
        supervisor = self.supervisor
        supervisor.start_service("coding")
        route = "coding"
        self.gateway.busy_services.add(route)
        original_sleep = self.clock.sleep

        def become_idle(seconds: float) -> None:
            self.gateway.busy_services.discard(route)
            original_sleep(seconds)

        self.clock.sleep = become_idle
        drained = supervisor.drain_service("coding")

        assert drained.state == "drained"
        assert drained.route == route
        assert self.gateway.routes[route][0] == "unavailable"

    def test_service_drain_times_out_without_stopping_a_busy_process(self) -> None:
        supervisor = self.supervisor
        transition = supervisor.start_service("coding")
        self.gateway.busy_services.add("coding")

        with pytest.raises(RuntimeError, match="active request"):
            supervisor.drain_service("coding")

        assert supervisor.service_status("coding").state == ServiceRunState.READY
        assert transition.run.pid is not None
        assert self.processes.processes[transition.run.pid].terminate_calls == 0
        assert self.store.operation_items
        operation_ids = set(self.store.operation_items)
        assert all(
            event["operation_id"] in operation_ids for event in self.store.event_items
        )

    def test_service_removal_drops_the_stopped_gateway_route(self) -> None:
        self.supervisor.start_service("coding")

        removed = self.supervisor.remove_service("coding")

        assert removed.run.state == ServiceRunState.STOPPED
        assert "coding" not in self.gateway.routes

    def test_one_service_failure_does_not_stop_another(self) -> None:
        coding = self.supervisor.start_service("coding")
        memory = self.supervisor.start_service("memory")
        assert coding.run.pid is not None
        self.processes.processes[coding.run.pid].running = False

        failed = self.supervisor.service_status("coding")
        healthy = self.supervisor.service_status("memory")

        assert failed.state == ServiceRunState.FAILED
        assert healthy.state == ServiceRunState.READY
        assert memory.run.pid is not None
        assert self.processes.processes[memory.run.pid].running

    def test_recovery_attaches_only_when_persisted_process_identity_matches(self):
        identity = ProcessIdentity(1234, "birth-1234")
        process = FakeProcess(1234)
        self.processes.processes[1234] = process
        self.probe.identities[1234] = identity
        self.store.snapshot_items.append(
            {
                "kind": "service_run",
                "id": "coding/run-old",
                "version": 1,
                "service": "coding",
                "run_id": "run-old",
                "state": "ready",
                "pid": 1234,
                "process_identity": "birth-1234",
                "upstream_port": 49199,
            }
        )

        recovered = self.supervisor.start().runs[0]

        assert recovered.run_id == "run-old"
        assert self.processes.attached == [1234]

        self.processes.attached.clear()
        self.probe.identities[1234] = ProcessIdentity(1234, "reused-pid")
        other = self._new_supervisor()
        other.start()
        assert self.processes.attached == []
        assert process.running

    def test_critical_pressure_stops_lru_idle_unpinned_but_never_pinned_or_busy(self):
        self.desired.items["pinned"] = _service("pinned", pinned=True)
        self.supervisor.start_service("coding")
        self.supervisor.start_service("memory")
        self.supervisor.start_service("pinned")
        self.gateway.last_used = {"coding": 10, "memory": 20, "pinned": 1}
        self.gateway.busy_services = {"memory"}
        self.pressure.level = PressureLevel.CRITICAL

        result = self.supervisor.reconcile_pressure()

        assert result.stopped_services == ("coding",)
        assert result.operator_stop_plan == ("memory", "pinned")
        assert self.supervisor.service_status("pinned").state == ServiceRunState.READY
        assert self.supervisor.service_status("memory").state == ServiceRunState.READY
        assert ("shed", True) in self.gateway.calls
        blocked = self.supervisor.start_service("coding")
        assert blocked.run.state == ServiceRunState.REJECTED

    def test_gateway_route_lookup_never_starts_stopped_service(self) -> None:
        self.supervisor.start()
        before = len(self.processes.launched)

        route = self.supervisor.resolve("coding")

        assert route is not None
        assert route.state == "stopped"
        assert len(self.processes.launched) == before

    def test_maintenance_detects_exit_and_registers_new_desired_route(self) -> None:
        transition = self.supervisor.start_service("coding")
        assert transition.run.pid is not None
        assert transition.run.pid is not None
        self.processes.processes[transition.run.pid].running = False
        self.desired.items["new"] = _service("new")

        outcome = self.supervisor.maintain()

        assert self.supervisor.service_status("coding").state == ServiceRunState.FAILED
        assert self.gateway.routes["new"] == ("stopped", None)
        assert outcome.pressure == PressureLevel.NORMAL

    def test_maintenance_restarts_a_running_service_after_desired_edit(self) -> None:
        first = self.supervisor.start_service("coding")
        self.desired.items["coding"] = _service("coding", route="coding-v2")

        outcome = self.supervisor.maintain()

        current = self.supervisor.service_status("coding")
        assert current.state == ServiceRunState.READY
        assert current.run_id != first.run.run_id
        assert "coding" not in self.gateway.routes
        assert self.gateway.routes["coding-v2"][0] == "ready"
        assert outcome.restarted_services == ("coding",)

    def test_maintenance_restarts_idle_run_after_exact_launch_target_update(
        self,
    ) -> None:
        first = self.supervisor.start_service("coding")
        self.runtime.revision = "v2"

        outcome = self.supervisor.maintain()

        current = self.supervisor.service_status("coding")
        assert current.state == ServiceRunState.READY
        assert current.run_id != first.run.run_id
        assert outcome.restarted_services == ("coding",)

    def _new_supervisor(self):
        candidate = Supervisor(
            desired_state=self.desired,
            runtime_supply=self.runtime,
            state_store=self.store,
            gateway=self.gateway,
            processes=self.processes,
            probe=self.probe,
            memory_pressure=self.pressure,
            clock=self.clock,
        )
        return candidate
