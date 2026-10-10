import json
import os
import subprocess
import sys

import pytest
from click import unstyle
from typer.testing import CliRunner

from mlxctl.application.catalogue import build_operation_catalogue
from mlxctl.application.dispatch import ApplicationError, OperationResult
from mlxctl.interfaces.cli import build_cli


class _Dispatcher:
    def __init__(self) -> None:
        self.requests = []
        self.previews = []

    def preview(self, request):
        self.previews.append(request)
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
        if request.name == "doctor":
            raise ApplicationError(
                "repair_required",
                "OptiQ capability conflict",
                next_actions=("mlxctl runtime update optiq",),
            )
        return OperationResult(
            request.name,
            {
                "operation": request.name,
                "parameters": dict(request.parameters),
            },
        )


class TestCliV1:
    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.dispatcher = _Dispatcher()
        self.tui_calls = 0

        def launch_tui() -> int:
            self.tui_calls += 1
            return 0

        self.app = build_cli(
            self.dispatcher,
            build_operation_catalogue(),
            tui_launcher=launch_tui,
        )
        self.runner = CliRunner()

    def test_root_help_exposes_resource_groups_and_guided_setup(self) -> None:
        result = self.runner.invoke(self.app, ["--help"])

        assert result.exit_code == 0, result.output
        assert "setup" in result.output
        assert "remove" in result.output
        assert "supervisor" in result.output
        assert "runtime" in result.output
        assert "model" in result.output
        assert "service" in result.output

    def test_every_catalogue_operation_has_a_cli_help_surface(
        self, subtests: pytest.Subtests
    ) -> None:
        for name in build_operation_catalogue():
            with subtests.test(operation=name):
                result = self.runner.invoke(self.app, [*name.split("."), "--help"])
                assert result.exit_code == 0, result.output

    def test_status_help_is_machine_overview_not_ambiguous_server_argument(
        self,
    ) -> None:
        result = self.runner.invoke(self.app, ["status", "--help"])

        assert result.exit_code == 0, result.output
        assert "SERVER" not in result.output
        assert "Supervisor" in result.output
        assert "Gateway" in result.output

    def test_nested_resource_command_dispatches_named_resource(self) -> None:
        result = self.runner.invoke(self.app, ["service", "stop", "coding", "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["operation"] == "service.stop"
        assert payload["parameters"]["resource"] == "coding"

    def test_service_edit_can_explicitly_clear_a_boolean(self) -> None:
        result = self.runner.invoke(
            self.app,
            ["service", "edit", "coding", "--no-pinned", "--yes", "--json"],
        )

        assert result.exit_code == 0, result.output
        assert self.dispatcher.requests[-1].parameters["pinned"] is False

    def test_help_and_dispatch_expose_operation_specific_values(self) -> None:
        help_result = self.runner.invoke(self.app, ["runtime", "install", "--help"])
        assert help_result.exit_code == 0, help_result.output
        assert "RUNTIME" in help_result.output
        assert "mlx_lm" in help_result.output
        assert "mlx_vlm" in help_result.output
        assert "optiq" in help_result.output

        result = self.runner.invoke(
            self.app,
            [
                "model",
                "search",
                "Qwen",
                "--source",
                "curated",
                "--limit",
                "8",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        assert dict(self.dispatcher.requests[-1].parameters) == {
            "query": "Qwen",
            "source": "curated",
            "limit": 8,
        }

    def test_model_cache_is_a_real_nested_command_group(self) -> None:
        result = self.runner.invoke(
            self.app,
            ["model", "cache", "evict", "qwen-exact", "--yes", "--json"],
        )

        assert result.exit_code == 0, result.output
        assert self.dispatcher.requests[-1].name == "model.cache.evict"
        assert self.dispatcher.requests[-1].parameters["confirmed"]

    def test_destructive_command_requires_prompt_or_explicit_yes(self) -> None:
        denied = self.runner.invoke(
            self.app,
            ["model", "cache", "evict", "qwen-exact", "--json"],
        )

        assert denied.exit_code != 0
        assert not self.dispatcher.requests

    def test_interactive_mutation_renders_backend_plan_before_confirmation(
        self,
    ) -> None:
        result = self.runner.invoke(
            self.app,
            ["service", "remove", "coding"],
            input="n\n",
        )

        assert result.exit_code != 0
        assert "Resolved mutation plan" in result.output
        assert self.dispatcher.previews[-1].name == "service.remove"
        assert not self.dispatcher.requests

    def test_setup_confirmation_carries_the_reviewed_plan_fingerprint(self) -> None:
        result = self.runner.invoke(self.app, ["setup"], input="y\n")

        assert result.exit_code == 0, result.output
        assert (
            self.dispatcher.requests[-1].parameters["plan_fingerprint"]
            == "sha256:exact"
        )

    def test_noninteractive_setup_previews_and_parses_structured_inputs(self) -> None:
        result = self.runner.invoke(
            self.app,
            [
                "setup",
                "--service-options",
                '{"kv_config":"kv_config.json","mtp":true}',
                "--clients",
                '["codex","hindsight"]',
                "--yes",
                "--json",
            ],
        )

        assert result.exit_code == 0, result.output
        assert self.dispatcher.previews[-1].name == "setup"
        parameters = self.dispatcher.requests[-1].parameters
        assert parameters["service_options"]["kv_config"] == "kv_config.json"
        assert parameters["service_options"]["mtp"]
        assert parameters["clients"] == ["codex", "hindsight"]
        assert parameters["plan_fingerprint"] == "sha256:exact"

    @pytest.mark.parametrize("color", [False, True])
    def test_setup_help_explains_capacity_choices_and_concurrency(
        self, color: bool
    ) -> None:
        environment = os.environ.copy()
        for key in (
            "NO_COLOR",
            "FORCE_COLOR",
            "PY_COLORS",
            "GITHUB_ACTIONS",
            "_TYPER_FORCE_DISABLE_TERMINAL",
        ):
            environment.pop(key, None)
        environment.update(TERM="xterm" if color else "dumb", COLUMNS="80")
        environment["FORCE_COLOR" if color else "NO_COLOR"] = "1"
        result = subprocess.run(
            [sys.executable, "-m", "mlxctl.entrypoints", "setup", "--help"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        output = unstyle(result.stdout)
        assert (result.stdout != output) is color
        assert "--capacity" in output
        assert "balanced" in output
        assert "long-context" in output
        assert "native-context" in output
        assert "simultaneous inference requests" in output
        assert "prefill at 4-7 requests" in output
        assert "8 permits" in output

    def test_machine_errors_are_stable_and_human_errors_offer_next_action(self) -> None:
        machine = self.runner.invoke(self.app, ["doctor", "--json"])
        assert machine.exit_code == 1
        assert json.loads(machine.output)["error"]["code"] == "repair_required"

        human = self.runner.invoke(self.app, ["doctor"])
        assert human.exit_code == 1
        assert "OptiQ capability conflict" in human.output
        assert "mlxctl runtime update optiq" in human.output

    def test_check_returns_nonzero_when_the_reported_state_is_unhealthy(self) -> None:
        original = self.dispatcher.execute

        def unhealthy(request):
            if request.name == "check":
                return OperationResult("check", {"state": "stopped", "checks": []})
            return original(request)

        self.dispatcher.execute = unhealthy
        result = self.runner.invoke(self.app, ["check", "--json"])

        assert result.exit_code == 1, result.output
        assert json.loads(result.output)["state"] == "stopped"

    def test_explicit_tui_command_uses_injected_launcher(self) -> None:
        result = self.runner.invoke(self.app, ["tui"])

        assert result.exit_code == 0, result.output
        assert self.tui_calls == 1
        assert not self.dispatcher.requests
