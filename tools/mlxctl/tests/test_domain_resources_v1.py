import pytest

from mlxctl.domain.resources import (
    ActivationPolicy,
    CachedRevision,
    InferenceService,
    ModelAlias,
    ModelInstallation,
    ModelRevision,
    ResourceName,
    RuntimeFamily,
    RuntimeInstallation,
    ServiceRun,
    ServiceRunState,
)


class TestResourceIdentity:
    def test_model_intent_cache_and_alias_are_distinct(self) -> None:
        revision = ModelRevision("mlx-community/Qwen", "a" * 40)
        cached = CachedRevision(revision, complete=True, size_bytes=42)
        installation = ModelInstallation("qwen-exact", revision)
        alias = ModelAlias(ResourceName("coding-model"), installation.name)

        assert cached.revision == installation.revision
        assert cached != installation
        assert alias.installation_name == "qwen-exact"

    def test_service_desired_state_is_separate_from_run(self) -> None:
        service = InferenceService(
            name=ResourceName("coding"),
            model_alias=ResourceName("coding-model"),
            runtime_installation="optiq@0.2.18",
            route=ResourceName("coding"),
            activation=ActivationPolicy.MANUAL,
            pinned=True,
            options={"mtp": True},
        )
        run = ServiceRun(
            run_id="run-1",
            service_name=service.name,
            state=ServiceRunState.READY,
            upstream_port=49152,
        )

        assert service.pinned
        assert run.service_name == service.name
        assert run.upstream_port == 49152

    def test_runtime_installation_has_exact_family_version_and_provenance(self) -> None:
        runtime = RuntimeInstallation(
            installation_id="mlx-lm@0.31.3",
            family=RuntimeFamily.MLX_LM,
            version="0.31.3",
            provenance="mlxctl-tested",
            capabilities=frozenset({"chat_completions", "max_context"}),
        )

        assert runtime.family == RuntimeFamily.MLX_LM
        assert "max_context" in runtime.capabilities

    def test_invalid_resource_names_and_mutable_revisions_are_rejected(
        self, subtests: pytest.Subtests
    ) -> None:
        for invalid in ("", "has space", "../escape", "/absolute", "ümlaut"):
            with subtests.test(invalid=invalid), pytest.raises(ValueError):
                ResourceName(invalid)
        with pytest.raises(ValueError, match="immutable commit SHA"):
            ModelRevision("org/model", "main")
