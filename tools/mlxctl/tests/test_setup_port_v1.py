import json
from collections.abc import Mapping

import pytest

from mlxctl.application.dispatch import ApplicationError
from mlxctl.application.setup import (
    CapacityProfile,
    ExactSetupSelection,
    RecommendedProfile,
    RemovalInventory,
    SetupPlanner,
    SetupPreflight,
)
from mlxctl.infrastructure.setup_port import (
    OperationalSetupEvidenceStore,
    SetupOperationPort,
)

GIB = 1024**3


class FakeOwner:
    def __init__(self, results=None, *, fail=None):
        self.calls = []
        self.results = dict(results or {})
        self.fail = fail

    def execute(self, operation, parameters):
        self.calls.append((operation, dict(parameters)))
        if operation == self.fail:
            raise RuntimeError(f"{operation} interrupted")
        result = self.results.get(operation, {})
        return dict(result(parameters) if callable(result) else result)


class FakeEvidenceStore:
    def __init__(self):
        self.items = {"setup": [], "removal": []}

    def load(self, scope):
        return tuple(self.items[scope])

    def record(self, scope, evidence):
        self.items[scope].append(evidence)


class FakeOperationalState:
    def __init__(self):
        self.rows = []

    def put_snapshot(self, snapshot):
        self.rows.append(dict(snapshot))
        return dict(snapshot)

    def snapshots(self, kind):
        return tuple(row for row in self.rows if row["kind"] == kind)


def selection(*, revision="2" * 40, trust=()):
    return ExactSetupSelection(
        runtime_name="optiq",
        runtime_version="0.3.3",
        runtime_lock_digest="sha256:" + "a" * 64,
        model_repository="mlx-community/Qwen-OptiQ-4bit",
        model_revision=revision,
        trust_grants=trust,
        service_name="coding",
        model_alias="qwen-optiq",
        service_route="engineering",
        activation="supervisor",
        pinned=True,
        service_options={
            "kv_config": "kv_config.json",
            "mtp": True,
            "runtime": {"draft_tokens": 4},
        },
        gateway_endpoint="http://127.0.0.1:8766/v1",
        clients=("codex", "hindsight"),
        client_options={"hindsight": {"profile": "default"}},
        sampling_profiles={
            "coding": {"temperature": 0.0, "top_p": 0.95},
            "reflect": {"temperature": 0.9, "top_p": 0.95},
        },
        context_window=32768,
    )


class TestSetupOperationPort:
    @pytest.fixture(autouse=True)
    def _setup(self):
        compact = RecommendedProfile("compact", 16 * GIB, selection(revision="1" * 40))
        workstation = RecommendedProfile("workstation", 64 * GIB, selection())
        capacities = (
            CapacityProfile(
                "balanced",
                "Balanced",
                131_072,
                6,
                5_737_807_872,
                2 * GIB,
                "Parallel work.",
            ),
            CapacityProfile(
                "long-context",
                "Long context",
                196_608,
                4,
                5_737_807_872,
                2 * GIB,
                "Larger requests.",
            ),
        )
        self.planner = SetupPlanner(
            (compact, workstation),
            capacity_profiles=capacities,
        )
        self.facts = SetupPreflight("darwin", "arm64", 96 * GIB, 500 * GIB, True)
        self.runtime = FakeOwner(
            {
                "runtime.install": {
                    "installation_id": "optiq-0.3.3-tested",
                    "runtime": "optiq",
                    "version": "0.3.3",
                    "provenance": "tested",
                    "bundle_id": "optiq-0.3.3-py3.13-macos-arm64",
                    "lock_sha256": "a" * 64,
                }
            }
        )
        self.model = FakeOwner(
            {
                "model.install": {
                    "installation_id": "qwen-optiq@" + "2" * 40,
                    "alias": "coding",
                    "revision": "2" * 40,
                }
            }
        )
        self.config = FakeOwner()
        self.clients = FakeOwner()
        self.supervisor = FakeOwner()
        self.verifier = FakeOwner(
            {"verify.request": {"ok": True, "text": "mlxctl ready"}}
        )
        self.evidence = FakeEvidenceStore()
        self.inventory = RemovalInventory(
            running_services=("coding",),
            registered=True,
            client_integrations=("codex", "hindsight"),
            product_owned_paths=("~/.config/mlxctl", "~/.local/state/mlxctl"),
            product_owned_bytes=2 * GIB,
            shared_cache_paths=("~/.cache/huggingface/hub/models--qwen",),
            shared_cache_bytes=40 * GIB,
            unrelated_settings=("Codex theme", "Hindsight bank ID"),
        )

    def port(self, *, model=None, facts=None):
        return SetupOperationPort(
            self.planner,
            preflight=lambda offline: (
                facts
                or SetupPreflight(
                    self.facts.platform,
                    self.facts.machine,
                    self.facts.memory_bytes,
                    self.facts.disk_free_bytes,
                    self.facts.online and not offline,
                )
            ),
            runtime=self.runtime,
            model=model or self.model,
            config=self.config,
            clients=self.clients,
            supervisor=self.supervisor,
            verifier=self.verifier,
            evidence=self.evidence,
            removal_inventory=lambda: self.inventory,
        )

    def test_preview_is_exact_machine_aware_editable_and_side_effect_free(self):
        preview = self.port().preview({"profile": "recommended"})

        assert preview["state"] == "review_required"
        assert preview["profile"] == "workstation"
        assert preview["editable"]
        assert isinstance(preview["selection"], Mapping)
        assert preview["selection"]["runtime"] == "optiq==0.3.3"
        assert preview["selection"]["model_revision"] == "2" * 40
        assert preview["selection"]["model_alias"] == "qwen-optiq"
        assert preview["selection"]["service_route"] == "engineering"
        assert (
            preview["selection"]["client_options"]["hindsight"]["profile"] == "default"
        )
        assert preview["selection"]["activation"] == "supervisor"
        assert preview["selection"]["pinned"]
        assert preview["selection"]["service_options"]["mtp"]
        assert isinstance(preview["plan_fingerprint"], (list, tuple, Mapping, str))
        assert len(preview["plan_fingerprint"]) == 64
        assert isinstance(preview["steps"], (list, tuple))
        assert preview["steps"][-1]["id"] == "verify.request"
        assert (
            self.runtime.calls
            + self.model.calls
            + self.config.calls
            + self.clients.calls
            + self.supervisor.calls
        ) == []

    def test_capacity_choice_is_discoverable_and_changes_plan_identity(self):
        baseline = self.port().preview({})
        selected = self.port().preview({"capacity": "long-context"})

        assert "capacity" in selected
        assert isinstance(selected["capacity"], Mapping)
        assert selected["capacity"]["profile"] == "long-context"
        assert selected["capacity"]["context_window"] == 196_608
        assert selected["capacity"]["max_concurrent"] == 4
        assert "simultaneous inference requests" in selected["capacity"]["note"]
        assert baseline["plan_fingerprint"] != selected["plan_fingerprint"]

    def test_confirmed_exact_plan_orchestrates_owners_and_persists_evidence(self):
        port = self.port()
        preview = port.preview({})

        result = port.execute(
            "setup",
            {
                "confirmed": True,
                "plan_fingerprint": preview["plan_fingerprint"],
            },
        )

        assert result["state"] == "complete"
        assert self.runtime.calls[0] == (
            "runtime.install",
            {
                "runtime": "optiq",
                "channel": "tested",
                "expected_version": "0.3.3",
                "expected_lock_digest": "a" * 64,
                "confirmed": True,
            },
        )
        assert self.model.calls[0][0] == "model.install"
        assert self.model.calls[0][1]["revision"] == "2" * 40
        assert self.model.calls[0][1]["alias"] == "qwen-optiq"
        service = next(
            call for call in self.config.calls if call[0] == "service.create"
        )
        assert service[1]["resource"] == "coding"
        assert service[1]["runtime"] == "optiq-0.3.3-tested"
        assert service[1]["model_alias"] == "qwen-optiq"
        assert service[1]["route"] == "engineering"
        assert service[1]["activation"] == "supervisor"
        assert service[1]["pinned"]
        assert service[1]["options"] == {
            "kv_config": "kv_config.json",
            "mtp": True,
            "runtime": {"draft_tokens": 4},
        }
        assert self.supervisor.calls[0][0] == "supervisor.start"
        assert self.supervisor.calls[-1] == ("service.start", {"resource": "coding"})
        assert self.verifier.calls[-1][0] == "verify.request"
        assert self.verifier.calls[-1][1]["model"] == "engineering"
        assert {call[1]["service"] for call in self.clients.calls} == {"coding"}
        assert len(self.evidence.items["setup"]) == 9
        verification_evidence = self.evidence.items["setup"][-1].detail
        assert "mlxctl ready" not in verification_evidence
        assert "response_sha256" in verification_evidence

    def test_editing_service_identity_or_options_changes_plan_identity(self):
        port = self.port()
        baseline = port.preview({})
        route = port.preview({"service_route": "assistant"})
        options = port.preview(
            {"service_options": {"kv_config": "kv_config.json", "mtp": False}}
        )

        assert baseline["plan_fingerprint"] != route["plan_fingerprint"]
        assert baseline["plan_fingerprint"] != options["plan_fingerprint"]

    def test_explicit_revision_scoped_trust_is_applied_but_never_inferred(self):
        trusted = selection(trust=("remote_code",))
        preview = self.port().preview({"selection": trusted})
        self.port().execute(
            "setup",
            {
                "selection": trusted,
                "confirmed": True,
                "plan_fingerprint": preview["plan_fingerprint"],
            },
        )

        trust = next(call for call in self.config.calls if call[0] == "model.trust")
        assert trust[1]["accepted_risks"] == ("remote_code",)
        assert trust[1]["revision"] == "2" * 40

    def test_missing_or_changed_plan_fingerprint_never_mutates(self):
        port = self.port()
        review = port.execute("setup", {"confirmed": True})
        assert review["state"] == "review_required"

        with pytest.raises(ApplicationError) as raised:
            port.execute(
                "setup",
                {"confirmed": True, "plan_fingerprint": "0" * 64},
            )

        assert raised.value.code == "plan_changed"
        assert self.runtime.calls == []

    def test_expert_setup_requires_every_exact_identity_field(self) -> None:
        port = self.port()

        with pytest.raises(ApplicationError, match="expert setup requires"):
            port.preview({"profile": "expert", "model_repository": "acme/model"})

        preview = port.preview(
            {
                "profile": "expert",
                "runtime_name": "optiq",
                "runtime_version": "0.3.3",
                "runtime_lock_digest": "sha256:" + "a" * 64,
                "model_repository": "acme/model",
                "model_revision": "3" * 40,
                "trust_grants": (),
                "service_name": "assistant",
                "gateway_endpoint": "http://127.0.0.1:8766/v1",
            }
        )
        assert preview["profile"] == "custom"
        assert isinstance(preview["selection"], Mapping)
        assert preview["selection"]["service_name"] == "assistant"

    def test_resume_reuses_durable_runtime_evidence_after_interruption(self):
        failing_model = FakeOwner(fail="model.install")
        port = self.port(model=failing_model)
        preview = port.preview({})
        with pytest.raises(ApplicationError) as raised:
            port.execute(
                "setup",
                {
                    "confirmed": True,
                    "plan_fingerprint": preview["plan_fingerprint"],
                },
            )
        assert raised.value.code == "setup_interrupted"
        assert [item.step_id for item in self.evidence.items["setup"]] == [
            "preflight",
            "gateway.configure",
            "supervisor.activate",
            "runtime.install",
        ]

        resumed = self.port()
        resumed_preview = resumed.preview({})
        resumed.execute(
            "setup",
            {
                "confirmed": True,
                "plan_fingerprint": resumed_preview["plan_fingerprint"],
            },
        )

        assert len(self.runtime.calls) == 1
        assert self.model.calls[0][0] == "model.install"

    def test_offline_missing_artifacts_block_before_any_owner_runs(self):
        port = self.port()
        preview = port.preview({"offline": True})
        assert isinstance(preview["steps"], (list, tuple))
        runtime = next(
            step for step in preview["steps"] if step["id"] == "runtime.install"
        )
        assert runtime["state"] == "blocked"

        with pytest.raises(ApplicationError) as raised:
            port.execute(
                "setup",
                {
                    "offline": True,
                    "confirmed": True,
                    "plan_fingerprint": preview["plan_fingerprint"],
                },
            )

        assert raised.value.code == "offline_blocked"
        assert self.runtime.calls == []

    def test_removal_plan_retains_shared_cache_and_unrelated_settings(self):
        port = self.port()
        preview = port.preview_removal()

        assert preview["state"] == "review_required"
        assert preview["retained_paths"] == list(self.inventory.shared_cache_paths)
        assert preview["retained_settings"] == list(self.inventory.unrelated_settings)
        assert self.supervisor.calls + self.clients.calls + self.config.calls == []

        result = port.remove(
            {
                "confirmed": True,
                "plan_fingerprint": preview["plan_fingerprint"],
            }
        )

        assert result["state"] == "complete"
        assert [call[0] for call in self.supervisor.calls] == [
            "service.drain",
            "service.stop",
            "supervisor.unregister",
        ]
        assert [call[0] for call in self.clients.calls] == [
            "client.remove",
            "client.remove",
        ]
        state_remove = next(
            call for call in self.config.calls if call[0] == "state.remove"
        )
        assert state_remove[1]["paths"] == self.inventory.product_owned_paths
        assert self.inventory.shared_cache_paths[0] not in state_remove[1]["paths"]

    def test_operational_evidence_adapter_round_trips_content_free_evidence(self):
        state = FakeOperationalState()
        evidence = OperationalSetupEvidenceStore(state)
        port = self.port()
        plan = port.preview({})
        first = self.planner.plan(self.facts).steps[0]

        from mlxctl.application.setup import SetupEvidence

        evidence.record(
            "setup", SetupEvidence.complete(first, json.dumps({"ok": True}))
        )

        restored = evidence.load("setup")
        assert restored[0].step_id == "preflight"
        assert restored[0].fingerprint == first.fingerprint
        assert state.rows[0]["kind"] == "setup_evidence"
        assert "prompt" not in json.dumps(state.rows[0])
        assert isinstance(plan["plan_fingerprint"], (list, tuple, Mapping, str))
        assert len(plan["plan_fingerprint"]) == 64
