from threading import Event

import pytest
from textual.widgets import Button, Checkbox, Input, Label, Select, Static

from mlxctl.application.catalogue import build_operation_catalogue
from mlxctl.application.dispatch import ApplicationError, OperationResult
from mlxctl.interfaces.tui import (
    MlxctlApp,
    ServiceSnapshot,
    TuiSnapshot,
)


class _Dispatcher:
    def __init__(self) -> None:
        self.requests = []
        self.previews = []
        self.error = None
        self.result_value: dict[str, object] = {"state": "complete"}
        self.execution_gate = None

    def preview(self, request):
        self.previews.append(request)
        if self.error is not None:
            raise self.error
        value = {
            "state": "planned",
            "operation": request.name,
            "parameters": dict(request.parameters),
        }
        if request.name == "setup":
            value["plan_fingerprint"] = "sha256:exact"
        return OperationResult(request.name, value)

    def execute(self, request):
        self.requests.append(request)
        if self.execution_gate is not None:
            self.execution_gate.wait(timeout=2)
        if self.error is not None:
            raise self.error
        return OperationResult(request.name, self.result_value)


class _Snapshots:
    def snapshot(self) -> TuiSnapshot:
        return TuiSnapshot(
            supervisor="running",
            gateway="ready · 127.0.0.1:8766/v1",
            services=(
                ServiceSnapshot(
                    name="coding",
                    state="blocked",
                    model="qwen-optiq",
                    runtime="optiq@0.2.15",
                    route="coding",
                    pinned=True,
                    detail="--max-context is not advertised",
                ),
            ),
            active_operations=0,
            pressure="normal",
        )


@pytest.mark.asyncio(loop_scope="function")
class TestTuiV1:
    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.catalogue = build_operation_catalogue()
        self.dispatcher = _Dispatcher()
        self.app = MlxctlApp(self.dispatcher, self.catalogue, _Snapshots())

    async def test_operations_console_has_stable_nav_workspace_and_inspector(
        self,
    ) -> None:
        async with self.app.run_test(size=(140, 45)):
            assert self.app.query_one("#resource-nav") is not None
            assert self.app.query_one("#workspace") is not None
            assert self.app.query_one("#inspector") is not None
            body = str(self.app.query_one("#view-body", Static).content)
            assert "coding" in body
            assert "Pinned" in body
            assert "blocked" in body.lower()

    async def test_navigation_preserves_capability_and_changes_context(self) -> None:
        async with self.app.run_test(size=(140, 45)) as pilot:
            await pilot.click("#nav-services")
            title = str(self.app.query_one("#view-title", Static).content)
            assert title == "Inference Services"
            assert "coding" in str(self.app.query_one("#view-body", Static).content)

            await pilot.click("#nav-topology")
            topology = str(self.app.query_one("#view-body", Static).content)
            assert "Model → Runtime → Service → Gateway" in topology
            assert "qwen-optiq → optiq@0.2.15" in topology

    async def test_resource_views_query_live_read_only_operations(self) -> None:
        async with self.app.run_test(size=(120, 40)) as pilot:
            await pilot.click("#nav-models")

            assert self.dispatcher.requests[-1].name == "model.list"
            body = str(self.app.query_one("#view-body", Static).content)
            assert "State" in body
            assert "complete" in body

    async def test_context_actions_and_command_catalogue_are_discoverable(self) -> None:
        async with self.app.run_test(size=(140, 45)) as pilot:
            await pilot.click("#find-model")
            assert self.app.selected_operation == "model.search"

            self.app.show_view("commands")
            body = str(self.app.query_one("#view-body", Static).content)
            assert "Every CLI operation is available here" in body
            assert "model.search" in body
            assert "service.stop" in body
            assert "supervisor.stop" in body

    async def test_first_run_is_intent_first_and_shows_exact_plan_before_change(
        self,
    ) -> None:
        async with self.app.run_test(size=(120, 40)) as pilot:
            await pilot.click("#first-run")
            title = str(self.app.query_one("#view-title", Static).content)
            body = str(self.app.query_one("#view-body", Static).content)
            assert title == "setup"
            assert self.app.selected_operation == "setup"
            assert "complete plan" in body
            assert self.app.query_one("#operation-form").styles.display == "block"
            capacity = self.app.query_one("#parameter-capacity", Select)
            labels = {str(label) for label, _value in capacity._options}
            assert any("balanced" in label.lower() for label in labels)
            assert any("long-context" in label for label in labels)
            assert any("native-context" in label for label in labels)

    async def test_command_palette_exposes_every_catalogue_operation(self) -> None:
        async with self.app.run_test(size=(120, 40)) as pilot:
            assert self.app.available_operations == tuple(self.catalogue)
            await pilot.press("ctrl+p")
            assert self.app.screen_stack

    async def test_command_palette_opens_the_selected_operation_workbench(self) -> None:
        async with self.app.run_test(size=(120, 40)) as pilot:
            await pilot.press("ctrl+p")
            await pilot.press(*"resolve cache verify name")
            await pilot.pause()
            await pilot.press("down", "enter")
            await pilot.pause()

            assert self.app.selected_operation == "model.install"
            assert isinstance(self.app.query_one("#parameter-repository"), Input)

    async def test_operation_view_renders_parameter_specific_controls(self) -> None:
        async with self.app.run_test(size=(120, 45)) as pilot:
            await self.app.open_operation("service.create")

            assert (
                self.app.query_one("#parameter-service", Input).placeholder
                == "Required"
            )
            assert isinstance(self.app.query_one("#parameter-model_alias"), Input)
            assert isinstance(self.app.query_one("#parameter-runtime"), Input)
            assert isinstance(self.app.query_one("#parameter-route"), Input)
            assert isinstance(self.app.query_one("#parameter-pinned"), Checkbox)
            assert not self.app.query_one("#workspace-actions").display
            assert self.app.focused is not None
            assert self.app.focused.id == "parameter-service"
            labels = "\n".join(str(label.content) for label in self.app.query(Label))
            assert "Service · Argument · required" in labels
            assert "Model Alias · Option --model-alias · required" in labels

            await self.app.open_operation("runtime.install")
            runtime = self.app.query_one("#parameter-runtime", Select)
            runtime.focus()
            await pilot.press("enter", "o", "p", "t", "i", "q", "enter")
            assert runtime.value == "optiq"

            await self.app.open_operation("service.edit")
            assert isinstance(self.app.query_one("#parameter-pinned"), Select)

    async def test_service_edit_can_explicitly_clear_a_boolean(self) -> None:
        async with self.app.run_test(size=(120, 45)) as pilot:
            await self.app.open_operation("service.edit")
            self.app.query_one("#parameter-resource", Input).value = "coding"
            self.app.query_one("#parameter-pinned", Select).value = "false"

            self.app.query_one("#operation-submit", Button).press()
            await pilot.pause()
            self.app.query_one("#operation-confirm", Button).press()
            await pilot.pause()

            assert self.dispatcher.requests[-1].parameters["pinned"] is False

    async def test_long_operation_worker_keeps_navigation_responsive(self) -> None:
        gate = Event()
        self.dispatcher.execution_gate = gate
        try:
            async with self.app.run_test(size=(120, 45)) as pilot:
                await self.app.open_operation("model.search")
                self.app.query_one("#operation-submit", Button).press()
                await pilot.pause()

                await pilot.click("#nav-topology")

                assert (
                    str(self.app.query_one("#view-title", Static).content)
                    == "Resource topology"
                )
                assert not gate.is_set()
                gate.set()
                await pilot.pause()
        finally:
            gate.set()

    async def test_confirmed_mutation_shows_exact_plan_before_dispatch(self) -> None:
        async with self.app.run_test(size=(120, 45)) as pilot:
            await self.app.open_operation("model.install")
            self.app.query_one(
                "#parameter-repository", Input
            ).value = "mlx-community/Qwen3-4B-4bit"
            self.app.query_one("#parameter-revision", Input).value = "abc123"
            self.app.query_one("#parameter-alias", Input).value = "coding"

            self.app.query_one("#operation-submit", Button).press()
            await pilot.pause()

            assert self.dispatcher.requests == []
            assert self.dispatcher.previews[-1].name == "model.install"
            plan = str(self.app.query_one("#view-body", Static).content)
            assert "Complete mutation plan" in plan
            assert "Resolved backend plan" in plan
            assert "model.install" in plan
            assert "mlx-community/Qwen3-4B-4bit" in plan
            assert "revision: abc123" in plan
            assert "offline: False" in plan
            assert self.app.focused is not None
            assert self.app.focused.id == "operation-confirm"

            self.app.query_one("#operation-confirm", Button).press()
            await pilot.pause()
            assert len(self.dispatcher.requests) == 1
            assert dict(self.dispatcher.requests[0].parameters) == {
                "repository": "mlx-community/Qwen3-4B-4bit",
                "revision": "abc123",
                "alias": "coding",
                "confirmed": True,
            }
            assert self.app.focused is not None
            assert self.app.focused.id == "operation-submit"

    async def test_setup_confirmation_carries_the_reviewed_plan_fingerprint(
        self,
    ) -> None:
        async with self.app.run_test(size=(120, 45)) as pilot:
            await self.app.open_operation("setup")
            self.app.query_one("#operation-submit", Button).press()
            await pilot.pause()
            self.app.query_one("#operation-confirm", Button).press()
            await pilot.pause()

            assert (
                self.dispatcher.requests[-1].parameters["plan_fingerprint"]
                == "sha256:exact"
            )

    async def test_every_catalogue_operation_can_be_executed_from_tui(
        self, subtests: pytest.Subtests
    ) -> None:
        async with self.app.run_test(size=(140, 55)) as pilot:
            for name, operation in self.catalogue.items():
                with subtests.test(operation=name):
                    await self.app.open_operation(name)
                    for parameter in operation.parameters:
                        control = self.app.query_one(f"#parameter-{parameter.name}")
                        if parameter.required and isinstance(control, Input):
                            if parameter.value_type == "integer":
                                control.value = "1"
                            elif parameter.value_type == "json":
                                control.value = "[]"
                            else:
                                control.value = "example"
                        elif parameter.required and isinstance(control, Select):
                            control.value = parameter.accepted[0]
                    before = len(self.dispatcher.requests)
                    self.app.query_one("#operation-submit", Button).press()
                    await pilot.pause()
                    if operation.confirmation:
                        assert len(self.dispatcher.requests) == before
                        self.app.query_one("#operation-confirm", Button).press()
                        await pilot.pause()
                    assert len(self.dispatcher.requests) == before + 1
                    assert self.dispatcher.requests[-1].name == name
                    if operation.confirmation:
                        assert self.dispatcher.requests[-1].parameters["confirmed"]

    async def test_results_errors_and_next_actions_stay_in_the_workspace(self) -> None:
        async with self.app.run_test(size=(120, 45)) as pilot:
            self.dispatcher.result_value = {
                "state": "ready",
                "next_actions": ["mlxctl service start coding"],
            }
            await self.app.open_operation("status")
            self.app.query_one("#operation-submit", Button).press()
            await pilot.pause()
            success = str(self.app.query_one("#view-body", Static).content)
            assert "State" in success
            assert "ready" in success
            assert "Next actions" in success
            assert "mlxctl service start coding" in success

            self.dispatcher.error = ApplicationError(
                "runtime_probe_failed",
                "The selected runtime did not advertise a required capability.",
                next_actions=(
                    "inspect the runtime probe",
                    "install the tested runtime",
                ),
            )
            await self.app.open_operation("runtime.inspect")
            self.app.query_one("#parameter-resource", Input).value = "optiq@0.2.15"
            self.app.query_one("#operation-submit", Button).press()
            await pilot.pause()
            failure = str(self.app.query_one("#view-body", Static).content)
            assert "runtime_probe_failed" in failure
            assert "required capability" in failure
            assert "inspect the runtime probe" in failure
            assert "install the tested runtime" in failure

    async def test_read_only_browsing_dispatches_only_safe_queries(self) -> None:
        async with self.app.run_test(size=(120, 45)) as pilot:
            await pilot.click("#nav-models")
            await pilot.click("#nav-topology")
            await self.app.open_operation("model.search")

            assert [request.name for request in self.dispatcher.requests] == [
                "model.list"
            ]

    async def test_cancelled_plan_makes_no_change_and_preserves_inputs(self) -> None:
        async with self.app.run_test(size=(100, 40)) as pilot:
            await self.app.open_operation("service.remove")
            resource = self.app.query_one("#parameter-resource", Input)
            resource.value = "coding"
            self.app.query_one("#operation-submit", Button).press()
            await pilot.pause()
            self.app.query_one("#operation-cancel", Button).press()
            await pilot.pause()

            assert self.dispatcher.requests == []
            assert resource.value == "coding"
            body = str(self.app.query_one("#view-body", Static).content)
            assert "No changes made" in body
            assert "editable inputs" in body
            assert self.app.focused is not None
            assert self.app.focused.id == "operation-submit"

    async def test_required_and_integer_input_errors_are_shown_in_surface(self) -> None:
        async with self.app.run_test(size=(100, 40)) as pilot:
            await self.app.open_operation("service.create")
            self.app.query_one("#operation-submit", Button).press()
            await pilot.pause()
            required = str(self.app.query_one("#view-body", Static).content)
            assert "Service is required" in required
            assert self.dispatcher.requests == []

            await self.app.open_operation("model.search")
            self.app.query_one("#parameter-limit", Input).value = "many"
            self.app.query_one("#operation-submit", Button).press()
            await pilot.pause()
            integer = str(self.app.query_one("#view-body", Static).content)
            assert "Limit must be a whole number" in integer
            assert self.dispatcher.requests == []

    async def test_narrow_layout_keeps_complete_operation_controls(self) -> None:
        async with self.app.run_test(size=(72, 35)):
            await self.app.open_operation("model.search")

            assert self.app.query_one("#resource-nav").styles.display == "none"
            assert self.app.query_one("#inspector").styles.display == "none"
            assert self.app.query_one("#operation-form").display
            assert isinstance(self.app.query_one("#parameter-query"), Input)
            assert isinstance(self.app.query_one("#parameter-source"), Select)
            assert isinstance(self.app.query_one("#parameter-limit"), Input)

    async def test_help_explains_current_screen_and_shared_controls(self) -> None:
        async with self.app.run_test(size=(100, 35)) as pilot:
            await pilot.press("question_mark")
            body = str(self.app.query_one("#view-body", Static).content)
            assert "Ctrl+P" in body
            assert "same operation catalogue" in body
            assert "color" in body.lower()
