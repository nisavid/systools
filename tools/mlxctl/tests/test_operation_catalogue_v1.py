from collections.abc import MutableMapping
from typing import cast

import pytest

from mlxctl.application.catalogue import (
    OperationKind,
    ParameterKind,
    SupervisorRequirement,
    build_operation_catalogue,
)


class TestOperationCatalogue:
    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.catalogue = build_operation_catalogue()

    def test_contains_the_complete_approved_command_tree(self) -> None:
        required = {
            "setup",
            "remove",
            "status",
            "check",
            "doctor",
            "supervisor.start",
            "supervisor.stop",
            "gateway.routes",
            "runtime.available",
            "runtime.install",
            "model.search",
            "model.install",
            "model.adopt",
            "model.cache.evict",
            "service.create",
            "service.start",
            "service.stop",
            "operation.inspect",
            "client.configure",
            "config.restore",
            "logs",
            "metrics",
            "tui",
        }
        assert required.issubset(self.catalogue)

    def test_reads_never_activate_the_supervisor(
        self, subtests: pytest.Subtests
    ) -> None:
        for operation in self.catalogue.values():
            if operation.kind is OperationKind.QUERY:
                with subtests.test(operation=operation.name):
                    assert operation.supervisor is SupervisorRequirement.NEVER_START

    def test_local_desired_state_mutations_do_not_require_supervisor(
        self, subtests: pytest.Subtests
    ) -> None:
        for name in (
            "remove",
            "gateway.configure",
            "model.uninstall",
            "model.trust",
            "service.create",
            "service.edit",
            "client.configure",
            "client.remove",
            "config.import",
            "config.restore",
        ):
            with subtests.test(operation=name):
                assert (
                    self.catalogue[name].supervisor is SupervisorRequirement.NEVER_START
                )

    def test_supervisor_stop_never_starts_the_supervisor_it_is_stopping(self) -> None:
        assert (
            self.catalogue["supervisor.stop"].supervisor
            is SupervisorRequirement.NEVER_START
        )

    def test_mutations_declare_confirmation_and_machine_help(self) -> None:
        install = self.catalogue["model.install"]
        assert install.confirmation
        assert "exact revision" in install.summary.lower()
        assert install.examples
        assert "json" in install.output_modes

    def test_summaries_explain_user_visible_effects(self) -> None:
        assert (
            self.catalogue["model.search"].summary
            == "Search curated, Hugging Face, or local cached models."
        )
        assert (
            "drain and stop one service"
            in self.catalogue["service.stop"].summary.casefold()
        )
        assert (
            "desired and live state"
            in self.catalogue["service.list"].summary.casefold()
        )

    def test_parameters_explain_accepted_values_and_discovery(self) -> None:
        install = self.catalogue["runtime.install"]
        assert install.parameters[0].kind == ParameterKind.ARGUMENT
        assert install.parameters[0].accepted == ("mlx_lm", "mlx_vlm", "optiq")
        search = self.catalogue["model.search"]
        assert search.parameters[0].name == "query"
        assert search.parameters[1].accepted == ("curated", "broad", "local")
        assert self.catalogue["status"].parameters == ()
        service = self.catalogue["service.create"]
        required_options = {
            parameter.name
            for parameter in service.parameters
            if parameter.required and parameter.kind is ParameterKind.OPTION
        }
        assert required_options == {"model_alias", "runtime"}
        assert self.catalogue["client.configure"].parameters[0].accepted == (
            "codex",
            "hindsight",
        )
        rollback = self.catalogue["model.rollback"]
        assert [item.name for item in rollback.parameters] == ["resource", "target"]
        assert rollback.parameters[1].required
        adopt = self.catalogue["model.adopt"]
        assert [item.name for item in adopt.parameters] == [
            "repository",
            "revision",
            "path",
            "alias",
        ]
        assert adopt.parameters[1].required
        assert adopt.parameters[2].required
        assert self.catalogue["runtime.doctor"].parameters == ()
        assert self.catalogue["runtime.prune"].parameters == ()
        assert self.catalogue["model.cache.prune"].parameters == ()
        assert self.catalogue["doctor"].parameters == ()
        assert "operation.resume" not in self.catalogue
        assert "operation.follow" not in self.catalogue
        assert "operation.cancel" not in self.catalogue
        setup = {
            parameter.name: parameter
            for parameter in self.catalogue["setup"].parameters
        }
        assert setup["service_options"].value_type == "json"
        assert setup["clients"].value_type == "json"
        assert setup["activation"].accepted == ("manual", "supervisor")

    def test_cli_and_tui_capabilities_are_derived_from_same_entries(
        self, subtests: pytest.Subtests
    ) -> None:
        for operation in self.catalogue.values():
            with subtests.test(operation=operation.name):
                assert operation.cli
                assert operation.tui

    def test_catalogue_is_immutable_and_names_are_unique(self) -> None:
        with pytest.raises(TypeError):
            cast(MutableMapping[str, object], self.catalogue)["status"] = (
                self.catalogue["check"]
            )
        assert len(self.catalogue) == len(set(self.catalogue))
