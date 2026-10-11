from collections.abc import MutableMapping
from typing import cast

import pytest

from mlxctl.infrastructure.model_profiles import (
    ModelProfileCatalogue,
    ModelProfileDefinitionError,
)

REPOSITORY = "mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit"
REVISION = "70a3aa32c7feef511182bf16aa332f37e8d82014"


class TestModelProfileCatalogue:
    def test_builtin_profiles_are_exact_revision_knowledge(self) -> None:
        catalogue = ModelProfileCatalogue.load_builtin()

        general = catalogue.profile(REPOSITORY, REVISION, "general-thinking")
        coding = catalogue.profile(REPOSITORY, REVISION, "precise-coding-thinking")
        non_thinking = catalogue.profile(REPOSITORY, REVISION, "non-thinking")

        assert dict(general.parameters) == {
            "temperature": 1.0,
            "top_p": 0.95,
            "top_k": 20,
            "min_p": 0.0,
            "presence_penalty": 1.5,
            "repetition_penalty": 1.0,
            "enable_thinking": True,
        }
        assert coding.parameters["temperature"] == 0.6
        assert coding.parameters["presence_penalty"] == 0.0
        assert non_thinking.parameters["temperature"] == 0.7
        assert non_thinking.parameters["top_p"] == 0.8
        assert non_thinking.parameters["enable_thinking"] is False
        assert general.repository == REPOSITORY
        assert general.revision == REVISION
        assert general.source_url.startswith("https://")
        assert general.source_revision

    @pytest.mark.parametrize(
        ("repository", "revision", "profile"),
        [
            ("other/model", REVISION, "general-thinking"),
            (REPOSITORY, "0" * 40, "general-thinking"),
            (REPOSITORY, REVISION, "unknown"),
        ],
        ids=["repository", "revision", "profile"],
    )
    def test_unknown_repository_revision_or_profile_fails_closed(
        self, repository: str, revision: str, profile: str
    ) -> None:
        catalogue = ModelProfileCatalogue.load_builtin()
        with pytest.raises(KeyError):
            catalogue.profile(repository, revision, profile)

    def test_definition_shape_and_scalar_values_are_strict(
        self, subtests: pytest.Subtests
    ) -> None:
        valid = {
            "models": [
                {
                    "repository": REPOSITORY,
                    "revision": REVISION,
                    "source": {
                        "url": "https://example.test/model-card",
                        "revision": "1" * 40,
                    },
                    "profiles": [
                        {
                            "name": "general-thinking",
                            "temperature": 1.0,
                            "top_p": 0.95,
                            "top_k": 20,
                            "min_p": 0.0,
                            "presence_penalty": 1.5,
                            "repetition_penalty": 1.0,
                            "enable_thinking": True,
                        }
                    ],
                }
            ]
        }
        invalid_mutations = (
            lambda source: source["models"][0].update({"unknown": True}),
            lambda source: source["models"][0]["profiles"][0].update(
                {"temperature": True}
            ),
            lambda source: source["models"][0]["profiles"][0].update(
                {"temperature": float("nan")}
            ),
            lambda source: source["models"][0]["profiles"][0].update({"top_p": 0}),
            lambda source: source["models"][0]["profiles"][0].update({"top_k": 0}),
            lambda source: source["models"][0]["profiles"][0].update({"min_p": 1.1}),
            lambda source: source["models"][0]["profiles"][0].update(
                {"presence_penalty": 2.1}
            ),
            lambda source: source["models"][0]["profiles"][0].update(
                {"repetition_penalty": 0}
            ),
            lambda source: source["models"][0]["profiles"][0].update(
                {"repetition_penalty": float("inf")}
            ),
            lambda source: source["models"][0]["profiles"][0].update(
                {"enable_thinking": 1}
            ),
        )

        for mutate in invalid_mutations:
            with subtests.test(mutation=mutate):
                source = {
                    "models": [
                        {
                            **valid["models"][0],
                            "source": dict(valid["models"][0]["source"]),
                            "profiles": [dict(valid["models"][0]["profiles"][0])],
                        }
                    ]
                }
                mutate(source)
                with pytest.raises(ModelProfileDefinitionError):
                    ModelProfileCatalogue.from_mapping(source)

    def test_profiles_and_parameters_are_immutable(self) -> None:
        catalogue = ModelProfileCatalogue.load_builtin()
        profile = catalogue.profile(REPOSITORY, REVISION, "general-thinking")

        with pytest.raises(TypeError):
            cast(MutableMapping[str, object], profile.parameters)["temperature"] = 0.1
