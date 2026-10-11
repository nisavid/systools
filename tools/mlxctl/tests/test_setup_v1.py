from collections.abc import Mapping, MutableMapping
from dataclasses import replace
from typing import cast

import pytest

from mlxctl.application.setup import (
    CapacityProfile,
    ExactSetupSelection,
    PlanExecutionError,
    RecommendedProfile,
    RemovalInventory,
    SetupEvidence,
    SetupPlanner,
    SetupPreflight,
    SetupRequest,
    StepState,
    _fingerprint,
)

GIB = 1024**3


def _selection(*, service: str, revision: str) -> ExactSetupSelection:
    return ExactSetupSelection(
        runtime_name="optiq",
        runtime_version="0.2.18",
        runtime_lock_digest="sha256:" + "a" * 64,
        model_repository="mlx-community/example-OptiQ-4bit",
        model_revision=revision,
        trust_grants=(),
        service_name=service,
        gateway_endpoint="http://127.0.0.1:8766/v1",
        clients=("codex", "hindsight"),
        client_options={"hindsight": {"profile": "default"}},
        sampling_profiles={
            "coding": {"temperature": 0.0, "top_p": 0.95},
            "memory-reflect": {"temperature": 0.9, "top_p": 0.95},
        },
        service_options={"kv_config": "kv_config.json", "mtp": True},
    )


class TestSetupV1:
    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.compact = RecommendedProfile(
            "compact", 16 * GIB, _selection(service="compact", revision="1" * 40)
        )
        self.workstation = RecommendedProfile(
            "workstation",
            64 * GIB,
            _selection(service="coding", revision="2" * 40),
        )
        self.planner = SetupPlanner((self.compact, self.workstation))

    def test_capacity_profile_coherently_caps_service_and_clients(self) -> None:
        planner = SetupPlanner(
            (self.compact,),
            capacity_profiles=(
                CapacityProfile(
                    "balanced",
                    "Balanced",
                    context_window=131_072,
                    max_concurrent=6,
                    projected_kv_bytes=5_737_807_872,
                    prompt_cache_bytes=2 * GIB,
                    description="Most parallel agents with long context.",
                ),
                CapacityProfile(
                    "native-context",
                    "Native context",
                    context_window=262_144,
                    max_concurrent=3,
                    projected_kv_bytes=5_737_807_872,
                    prompt_cache_bytes=2 * GIB,
                    description="Longest context with fewer simultaneous requests.",
                ),
            ),
            default_capacity_profile="balanced",
        )
        facts = SetupPreflight("darwin", "arm64", 48 * GIB, 200 * GIB, True)

        balanced = planner.preview(planner.plan(facts))
        native = planner.preview(
            planner.plan(facts, SetupRequest(capacity_profile="native-context"))
        )

        assert balanced.capacity_profile == "balanced"
        assert balanced.context_window == 131_072
        assert balanced.service_options["max_context"] == 131_072
        assert balanced.service_options["max_concurrent"] == 6
        assert balanced.service_options["prompt_cache_bytes"] == 2 * GIB
        assert balanced.projected_kv_bytes == 5_737_807_872
        assert native.capacity_profile == "native-context"
        assert native.context_window == 262_144
        assert native.service_options["max_concurrent"] == 3

    def test_selected_client_context_cannot_exceed_service_capacity(self) -> None:
        invalid = _selection(service="coding", revision="3" * 40)
        invalid = replace(
            invalid,
            service_options={
                "kv_config": "kv_config.json",
                "max_context": 131_072,
                "max_concurrent": 6,
            },
            context_window=196_608,
        )

        with pytest.raises(ValueError, match="context_window.*max_context"):
            invalid.validate_exact()

    def test_exact_selection_is_not_overwritten_by_default_capacity(self) -> None:
        planner = SetupPlanner(
            (self.compact,),
            capacity_profiles=(
                CapacityProfile(
                    "balanced",
                    "Balanced",
                    131_072,
                    6,
                    5_737_807_872,
                    2 * GIB,
                    "Default capacity.",
                ),
            ),
            default_capacity_profile="balanced",
        )
        exact = replace(
            _selection(service="coding", revision="3" * 40),
            service_options={
                "kv_config": "kv_config.json",
                "max_context": 262_144,
                "max_concurrent": 3,
            },
            context_window=262_144,
        )

        plan = planner.plan(
            SetupPreflight("darwin", "arm64", 48 * GIB, 200 * GIB, True),
            SetupRequest(selection=exact, noninteractive=True, confirmed=True),
        )

        assert plan.capacity_profile is None
        assert plan.selection.context_window == 262_144
        assert plan.selection.service_options["max_concurrent"] == 3

    def test_guided_plan_preselects_a_machine_aware_editable_exact_profile(
        self,
    ) -> None:
        plan = self.planner.plan(
            SetupPreflight(
                platform="darwin",
                machine="arm64",
                memory_bytes=96 * GIB,
                disk_free_bytes=300 * GIB,
                online=True,
            )
        )

        preview = self.planner.preview(plan)

        assert plan.profile_name == "workstation"
        assert preview.editable
        assert preview.runtime == "optiq==0.2.18"
        assert preview.model_revision == "2" * 40
        assert preview.service_name == "coding"
        assert preview.model_alias == "coding"
        assert preview.service_route == "coding"
        assert preview.activation == "manual"
        assert not preview.pinned
        assert preview.service_options["kv_config"] == "kv_config.json"
        assert preview.gateway_endpoint == "http://127.0.0.1:8766/v1"
        assert preview.clients == ("codex", "hindsight")
        assert preview.client_options["hindsight"]["profile"] == "default"
        assert preview.sampling_profiles["coding"]["temperature"] == 0.0

    def test_guided_setup_never_falls_back_to_an_oversized_profile(self) -> None:
        undersized = SetupPreflight(
            "darwin",
            "arm64",
            memory_bytes=8 * GIB,
            disk_free_bytes=8 * GIB,
            online=True,
        )

        with pytest.raises(ValueError, match="no recommended setup profile fits"):
            self.planner.plan(undersized)

        expert = self.planner.plan(
            undersized,
            SetupRequest(selection=self.compact.selection),
        )
        assert expert.profile_name == "custom"

    def test_service_identity_and_options_are_exact_immutable_plan_inputs(self) -> None:
        facts = SetupPreflight("darwin", "arm64", 64 * GIB, 200 * GIB, True)
        options = {
            "kv_config": "kv_config.json",
            "mtp": True,
            "runtime": {"draft_tokens": 4},
            "stop": ["</s>", 17],
        }
        exact = ExactSetupSelection(
            runtime_name="optiq",
            runtime_version="0.3.3",
            runtime_lock_digest="sha256:" + "a" * 64,
            model_repository="mlx-community/example",
            model_revision="3" * 40,
            trust_grants=(),
            service_name="internal-worker",
            model_alias="qwen-optiq",
            service_route="coding",
            activation="supervisor",
            pinned=True,
            service_options=options,
            gateway_endpoint="http://127.0.0.1:8766/v1",
        )

        plan = self.planner.plan(facts, SetupRequest(selection=exact))
        service = next(step for step in plan.steps if step.id == "service.configure")
        gateway = next(step for step in plan.steps if step.id == "gateway.configure")
        verify = next(step for step in plan.steps if step.id == "verify.request")

        options["mtp"] = False
        assert exact.service_options["mtp"]
        assert isinstance(exact.service_options["runtime"], Mapping)
        assert exact.service_options["runtime"]["draft_tokens"] == 4
        with pytest.raises(TypeError):
            cast(MutableMapping[str, object], exact.service_options)["mtp"] = False
        assert service.inputs["model_alias"] == "qwen-optiq"
        assert service.inputs["route"] == "coding"
        assert service.inputs["activation"] == "supervisor"
        assert service.inputs["pinned"]
        assert gateway.inputs["route"] == "coding"
        assert verify.inputs["model"] == "coding"

    def test_service_names_activation_and_json_options_are_validated(self) -> None:
        facts = SetupPreflight("darwin", "arm64", 64 * GIB, 200 * GIB, True)
        invalid_name = ExactSetupSelection(
            runtime_name="optiq",
            runtime_version="0.3.3",
            runtime_lock_digest="sha256:" + "a" * 64,
            model_repository="mlx-community/example",
            model_revision="3" * 40,
            trust_grants=(),
            service_name="not safe",
            gateway_endpoint="http://127.0.0.1:8766/v1",
        )
        with pytest.raises(ValueError, match="resource name"):
            self.planner.plan(facts, SetupRequest(selection=invalid_name))

        with pytest.raises(ValueError, match="service_options"):
            ExactSetupSelection(
                runtime_name="optiq",
                runtime_version="0.3.3",
                runtime_lock_digest="sha256:" + "a" * 64,
                model_repository="mlx-community/example",
                model_revision="3" * 40,
                trust_grants=(),
                service_name="coding",
                gateway_endpoint="http://127.0.0.1:8766/v1",
                service_options={"bad": {1, 2}},
            )

    def test_exact_noninteractive_setup_requires_explicit_trust_and_confirmation(
        self,
    ) -> None:
        facts = SetupPreflight("darwin", "arm64", 64 * GIB, 200 * GIB, True)
        incomplete = ExactSetupSelection(
            runtime_name="optiq",
            runtime_version="0.2.18",
            runtime_lock_digest="sha256:" + "a" * 64,
            model_repository="mlx-community/example",
            model_revision="3" * 40,
            trust_grants=None,
            service_name="coding",
            gateway_endpoint="http://127.0.0.1:8766/v1",
        )

        with pytest.raises(ValueError, match="trust_grants"):
            self.planner.plan(
                facts,
                SetupRequest(selection=incomplete, noninteractive=True, confirmed=True),
            )
        with pytest.raises(ValueError, match="confirmed"):
            self.planner.plan(
                facts,
                SetupRequest(
                    selection=self.workstation.selection,
                    noninteractive=True,
                    confirmed=False,
                ),
            )

        not_locked = ExactSetupSelection(
            runtime_name="optiq",
            runtime_version="0.2.18",
            runtime_lock_digest="sha256:short",
            model_repository="mlx-community/example",
            model_revision="3" * 40,
            trust_grants=(),
            service_name="coding",
            gateway_endpoint="http://127.0.0.1:8766/v1",
        )
        with pytest.raises(ValueError, match="runtime_lock_digest"):
            self.planner.plan(
                facts,
                SetupRequest(selection=not_locked, noninteractive=True, confirmed=True),
            )

        hostname_endpoint = ExactSetupSelection(
            runtime_name="optiq",
            runtime_version="0.3.3",
            runtime_lock_digest="sha256:" + "a" * 64,
            model_repository="mlx-community/example",
            model_revision="3" * 40,
            trust_grants=(),
            service_name="coding",
            gateway_endpoint="http://localhost:8766/v1",
        )
        with pytest.raises(ValueError, match="literal HTTP loopback"):
            self.planner.plan(
                facts,
                SetupRequest(
                    selection=hostname_endpoint,
                    noninteractive=True,
                    confirmed=True,
                ),
            )

    def test_resume_skips_a_completed_download_and_ends_with_a_real_request(
        self,
    ) -> None:
        facts = SetupPreflight("darwin", "arm64", 64 * GIB, 200 * GIB, True)
        initial = self.planner.plan(facts)
        model_step = next(step for step in initial.steps if step.id == "model.install")
        evidence = SetupEvidence.complete(model_step)
        resumed = self.planner.plan(facts, evidence=(evidence,))
        executed: list[str] = []

        result = self.planner.apply(
            resumed,
            lambda step: executed.append(step.id) or SetupEvidence.complete(step),
            evidence=(evidence,),
        )

        assert "model.install" not in executed
        assert executed[-1] == "verify.request"
        assert result.evidence[-1].step_id == "verify.request"
        assert result.complete

    def test_changed_supervisor_protocol_invalidates_old_activation_evidence(
        self,
    ) -> None:
        facts = SetupPreflight("darwin", "arm64", 64 * GIB, 200 * GIB, True)
        old_fingerprint = _fingerprint(
            "supervisor.activate",
            {"reason": "install runtimes, models, and start the selected service"},
        )
        old_evidence = SetupEvidence(
            "supervisor.activate", old_fingerprint, StepState.COMPLETE
        )

        resumed = self.planner.plan(facts, evidence=(old_evidence,))
        activation = next(
            step for step in resumed.steps if step.id == "supervisor.activate"
        )

        assert activation.state == StepState.READY
        assert activation.fingerprint != old_fingerprint

    def test_apply_records_only_completed_steps_before_a_failure(self) -> None:
        facts = SetupPreflight("darwin", "arm64", 64 * GIB, 200 * GIB, True)
        plan = self.planner.plan(facts)
        recorded: list[SetupEvidence] = []

        def execute(step):
            if step.id == "model.install":
                raise RuntimeError("download interrupted")
            return SetupEvidence.complete(step)

        with pytest.raises(PlanExecutionError) as failure:
            self.planner.apply(plan, execute, record=recorded.append)

        assert failure.value.step_id == "model.install"
        assert [item.step_id for item in recorded] == [
            "preflight",
            "gateway.configure",
            "supervisor.activate",
            "runtime.install",
        ]

    def test_offline_plan_exposes_evidence_and_blocks_missing_network_artifacts(
        self,
    ) -> None:
        plan = self.planner.plan(
            SetupPreflight("darwin", "arm64", 64 * GIB, 200 * GIB, False)
        )

        assert plan.offline
        runtime = next(step for step in plan.steps if step.id == "runtime.install")
        model = next(step for step in plan.steps if step.id == "model.install")
        assert runtime.state == StepState.BLOCKED
        assert "offline" in runtime.reason
        assert model.state == StepState.BLOCKED
        assert "No completed evidence" in self.planner.preview(plan).offline_note

    def test_removal_is_reference_aware_and_retains_shared_and_unrelated_state(
        self,
    ) -> None:
        inventory = RemovalInventory(
            running_services=("coding",),
            registered=True,
            client_integrations=("codex", "hindsight"),
            product_owned_paths=("~/.config/mlxctl", "~/.local/state/mlxctl"),
            product_owned_bytes=2 * GIB,
            shared_cache_paths=("~/.cache/huggingface/hub/models--example",),
            shared_cache_bytes=40 * GIB,
            references={"coding": ("optiq@0.2.18", "example@" + "2" * 40)},
            unrelated_settings=("Codex theme", "Hindsight bank ID"),
        )

        plan = self.planner.plan_removal(inventory)

        assert tuple(step.id for step in plan.steps) == (
            "service.drain",
            "service.stop",
            "supervisor.unregister",
            "client.remove",
            "state.remove",
        )
        assert plan.freed_bytes_estimate == 2 * GIB
        assert plan.retained_paths == inventory.shared_cache_paths
        assert plan.retained_settings == inventory.unrelated_settings
        assert "coding" in plan.references
