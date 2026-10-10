import pytest
import tomlkit

from mlxctl.application.config_schema import ConfigSchemaError, validate_config

VALID = """
schema_version = 1

[gateway]
host = "127.0.0.1"
port = 8766

[runtimes."optiq@0.2.18"]
definition = "optiq"
version = "0.2.18"
provenance = "tested"
root = "/Users/example/.local/share/mlxctl/runtimes/optiq@0.2.18"
launcher = ["/Users/example/.local/share/mlxctl/runtimes/optiq@0.2.18/bin/optiq", "serve"]
capabilities = ["model", "host", "port", "kv_config", "mtp"]
bundle_id = "optiq-0.2.18-py313-macos-arm64"

[models.qwen-exact]
repository = "mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit"
revision = "70a3aa32c7feef511182bf16aa332f37e8d82014"

[aliases.qwen-optiq]
installation = "qwen-exact"

[services.coding]
model_alias = "qwen-optiq"
runtime = "optiq@0.2.18"
route = "coding"
activation = "manual"
pinned = true

[services.coding.options]
kv_config = "kv_config.json"
mtp = true

[clients.codex]
kind = "codex"
service = "coding"
context_window = 32768
provider = "mlx-local"

[clients.codex.sampling.coding]
temperature = 0.6
top_p = 0.95
top_k = 20
min_p = 0.0
presence_penalty = 0.0
repetition_penalty = 1.0
enable_thinking = true
upstream_profile = "precise-coding-thinking"
source_url = "https://huggingface.co/Qwen/Qwen3.6-35B-A3B/blob/995ad96eacd98c81ed38be0c5b274b04031597b0/README.md#best-practices"
source_revision = "995ad96eacd98c81ed38be0c5b274b04031597b0"
"""


class TestConfigSchemaV1:
    def test_loads_distinct_runtime_model_alias_service_gateway_and_client_state(
        self,
    ) -> None:
        config = validate_config(tomlkit.parse(VALID))

        assert config.gateway.port == 8766
        assert config.runtimes["optiq@0.2.18"].definition == "optiq"
        assert "mtp" in config.runtimes["optiq@0.2.18"].capabilities
        assert config.models["qwen-exact"].revision.revision[:8] == "70a3aa32"
        assert config.models["qwen-exact"].provenance == "cached"
        assert config.models["qwen-exact"].path is None
        assert config.aliases["qwen-optiq"].installation_name == "qwen-exact"
        assert config.services["coding"].pinned
        assert config.services["coding"].route == "coding"
        assert config.clients["codex"].service == "coding"
        assert config.clients["codex"].context_window == 32768
        assert config.clients["codex"].provider == "mlx-local"
        assert config.clients["codex"].sampling["coding"].top_p == 0.95
        assert config.clients["codex"].sampling["coding"].top_k == 20
        assert config.clients["codex"].sampling["coding"].min_p == 0.0
        assert config.clients["codex"].sampling["coding"].presence_penalty == 0.0
        assert config.clients["codex"].sampling["coding"].repetition_penalty == 1.0
        assert config.clients["codex"].sampling["coding"].enable_thinking
        assert (
            config.clients["codex"].sampling["coding"].upstream_profile
            == "precise-coding-thinking"
        )
        assert (
            config.clients["codex"].sampling["coding"].source_revision
            == "995ad96eacd98c81ed38be0c5b274b04031597b0"
        )

    def test_rejects_unknown_keys_raw_argv_and_environment_escape_hatches(
        self, subtests: pytest.Subtests
    ) -> None:
        for insertion in (
            "mystery = true\n",
            'arguments = ["--unsafe"]\n',
            'environment = { TOKEN = "secret" }\n',
        ):
            source = VALID.replace("pinned = true\n", f"pinned = true\n{insertion}")
            with (
                subtests.test(insertion=insertion),
                pytest.raises(ConfigSchemaError),
            ):
                validate_config(tomlkit.parse(source))

    def test_rejects_non_loopback_gateway_and_duplicate_routes(self) -> None:
        with pytest.raises(ConfigSchemaError, match="loopback"):
            validate_config(tomlkit.parse(VALID.replace("127.0.0.1", "0.0.0.0")))
        duplicate = (
            VALID
            + """
[services.memory]
model_alias = "qwen-optiq"
runtime = "optiq@0.2.18"
route = "coding"
"""
        )
        with pytest.raises(ConfigSchemaError, match="Gateway route"):
            validate_config(tomlkit.parse(duplicate))

    def test_rejects_missing_references_and_mutable_model_revision(self) -> None:
        with pytest.raises(ConfigSchemaError, match="immutable commit SHA"):
            validate_config(
                tomlkit.parse(
                    VALID.replace("70a3aa32c7feef511182bf16aa332f37e8d82014", "main")
                )
            )
        with pytest.raises(ConfigSchemaError, match="unknown Model Alias"):
            validate_config(
                tomlkit.parse(
                    VALID.replace(
                        'model_alias = "qwen-optiq"', 'model_alias = "missing"'
                    )
                )
            )

    def test_adopted_model_requires_an_absolute_external_path(self) -> None:
        adopted = VALID.replace(
            'revision = "70a3aa32c7feef511182bf16aa332f37e8d82014"',
            'revision = "70a3aa32c7feef511182bf16aa332f37e8d82014"\n'
            'provenance = "adopted"\npath = "/Volumes/models/qwen"',
        )
        model = validate_config(tomlkit.parse(adopted)).models["qwen-exact"]
        assert model.provenance == "adopted"
        assert model.path == "/Volumes/models/qwen"
        with pytest.raises(ConfigSchemaError, match="absolute"):
            validate_config(
                tomlkit.parse(adopted.replace("/Volumes/models/qwen", "qwen"))
            )

    def test_rejects_unsupported_client_kind_and_invalid_sampling(
        self, subtests: pytest.Subtests
    ) -> None:
        with pytest.raises(ConfigSchemaError, match="client kind"):
            validate_config(
                tomlkit.parse(VALID.replace('kind = "codex"', 'kind = "other"'))
            )
        with pytest.raises(ConfigSchemaError, match="sampling"):
            validate_config(
                tomlkit.parse(
                    VALID.replace("temperature = 0.6", 'temperature = "cold"')
                )
            )

        for original, invalid in (
            ("top_k = 20", "top_k = -1"),
            ("min_p = 0.0", "min_p = 1.1"),
            ("presence_penalty = 0.0", "presence_penalty = 2.1"),
            ("repetition_penalty = 1.0", "repetition_penalty = 0.0"),
            ("enable_thinking = true", 'enable_thinking = "yes"'),
            ("enable_thinking = true", "preserve_thinking = 1"),
            (
                'upstream_profile = "precise-coding-thinking"',
                'upstream_profile = "../bad"',
            ),
            (
                'source_url = "https://huggingface.co/Qwen/Qwen3.6-35B-A3B/blob/995ad96eacd98c81ed38be0c5b274b04031597b0/README.md#best-practices"',
                'source_url = "http://example.test/model-card"',
            ),
            (
                'source_revision = "995ad96eacd98c81ed38be0c5b274b04031597b0"',
                'source_revision = "main"',
            ),
        ):
            with (
                subtests.test(invalid=invalid),
                pytest.raises(ConfigSchemaError, match="sampling"),
            ):
                validate_config(tomlkit.parse(VALID.replace(original, invalid)))

    def test_hindsight_profile_and_sampling_are_explicit_desired_state(self) -> None:
        source = (
            VALID.replace(
                "[clients.codex]",
                "[clients.hindsight]",
            )
            .replace(
                'kind = "codex"',
                'kind = "hindsight"\nprofile = "agent-memory"\nmax_concurrent = 1',
            )
            .replace(
                "[clients.codex.sampling.coding]",
                "[clients.hindsight.sampling.verification]",
            )
        )

        profile_body = VALID.split("[clients.codex.sampling.coding]\n", 1)[1]
        source += "\n[clients.hindsight.sampling.retain]\n" + profile_body
        source += "\n[clients.hindsight.sampling.reflect]\n" + profile_body
        source += "\n[clients.hindsight.sampling.consolidation]\n" + profile_body

        client = validate_config(tomlkit.parse(source)).clients["hindsight"]

        assert client.profile == "agent-memory"
        assert client.sampling["retain"].temperature == 0.6
        assert client.max_concurrent == 1

    def test_rejects_unsafe_hindsight_profile_and_ambiguous_flat_sampling(self) -> None:
        hindsight = VALID.replace("[clients.codex]", "[clients.hindsight]").replace(
            'kind = "codex"',
            'kind = "hindsight"\nprofile = "../default"\nmax_concurrent = 1',
        )
        with pytest.raises(ConfigSchemaError, match="profile"):
            validate_config(tomlkit.parse(hindsight))

        flat = VALID.replace(
            "[clients.codex.sampling.coding]", "[clients.codex.sampling]"
        )
        with pytest.raises(ConfigSchemaError, match="sampling profile"):
            validate_config(tomlkit.parse(flat))

    def test_rejects_partial_workload_sets_and_unrepresentable_codex_values(
        self, subtests: pytest.Subtests
    ) -> None:
        partial_hindsight = VALID.replace(
            "[clients.codex]", "[clients.hindsight]"
        ).replace(
            'kind = "codex"',
            'kind = "hindsight"\nprofile = "default"\nmax_concurrent = 1',
        )
        with pytest.raises(ConfigSchemaError, match="requires sampling profiles"):
            validate_config(tomlkit.parse(partial_hindsight))

        for original, invalid in (
            ("min_p = 0.0", "min_p = 0.1"),
            ("presence_penalty = 0.0", "presence_penalty = 1.5"),
            ("repetition_penalty = 1.0", "repetition_penalty = 1.1"),
            ("top_k = 20", "top_k = 20\nmax_tokens = 100"),
        ):
            with (
                subtests.test(invalid=invalid),
                pytest.raises(ConfigSchemaError, match="Responses"),
            ):
                validate_config(tomlkit.parse(VALID.replace(original, invalid)))

    def test_rejects_non_finite_sampling_values(
        self, subtests: pytest.Subtests
    ) -> None:
        for value in ("nan", "+inf", "-inf"):
            with (
                subtests.test(value=value),
                pytest.raises(ConfigSchemaError, match="finite"),
            ):
                validate_config(
                    tomlkit.parse(
                        VALID.replace("temperature = 0.6", f"temperature = {value}")
                    )
                )
