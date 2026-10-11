import hashlib
import json
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import cast

import pytest

from mlxctl.application.config_schema import validate_config
from mlxctl.infrastructure.config_store import ConfigStore
from mlxctl.infrastructure.control_protocol import MAX_FRAME_BYTES
from mlxctl.infrastructure.model_intelligence import (
    EvidenceState,
    ModelIntelligence,
    RepositoryFile,
    RuntimeCompatibility,
    TrustSignal,
)
from mlxctl.infrastructure.model_supply import (
    CacheDeletionPlan,
    CachedRevision,
    CacheInventory,
    ModelAlias,
    ModelInstallation,
    ModelInstallResult,
    ModelProvenance,
    ModelRevision,
    ModelSupply,
    VerificationResult,
)
from mlxctl.infrastructure.runtime_supply import (
    RuntimeCatalogue,
    RuntimeInstallation,
    RuntimeManager,
)
from mlxctl.infrastructure.state_store import OperationalStateStore
from mlxctl.infrastructure.supply_ports import (
    CacheMovePlan,
    ExactRevisionModelSecurity,
    ModelSecurityPolicyError,
    ModelSupplyPort,
    RuntimeSupplyPort,
    SupplyPortError,
    VerifiedCacheMover,
    inspect_adopted_snapshot,
    verify_adopted_snapshot,
)

_SHA_A = "a" * 40
_SHA_B = "b" * 40


class FakeRuntimeManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[tuple[object, ...]] = []

    def install_tested(
        self, bundle_id: str, installation_root: Path
    ) -> RuntimeInstallation:
        self.calls.append(("install_tested", bundle_id, installation_root))
        runtime = bundle_id.split("-", 1)[0]
        if bundle_id.startswith("mlx_lm"):
            runtime = "mlx_lm"
        elif bundle_id.startswith("mlx_vlm"):
            runtime = "mlx_vlm"
        version = bundle_id.removeprefix(f"{runtime}-").split("-py", 1)[0]
        installation = self._installation(
            bundle_id,
            runtime,
            version,
            "tested",
            bundle_id=bundle_id,
        )
        installation.root.mkdir(parents=True)
        return installation

    def install_custom(
        self,
        runtime: str,
        version: str,
        *,
        python: str,
        installation_root: Path,
    ) -> RuntimeInstallation:
        self.calls.append(
            ("install_custom", runtime, version, python, installation_root)
        )
        installation = self._installation(
            f"{runtime}-{version}-custom", runtime, version, "custom"
        )
        installation.root.mkdir(parents=True)
        return installation

    def adopt_custom(self, runtime: str, root: Path) -> RuntimeInstallation:
        self.calls.append(("adopt_custom", runtime, root))
        return self._installation(
            f"{runtime}-9.9-adopted", runtime, "9.9", "adopted", root=root
        )

    def _installation(
        self,
        installation_id: str,
        runtime: str,
        version: str,
        provenance: str,
        *,
        root: Path | None = None,
        bundle_id: str | None = None,
    ) -> RuntimeInstallation:
        path = root or self.root / installation_id
        return RuntimeInstallation(
            installation_id=installation_id,
            runtime=runtime,
            version=version,
            provenance=provenance,
            root=path.resolve(),
            launcher=(str(path.resolve() / "bin" / runtime), "serve"),
            capabilities=frozenset({"model", "host", "port"}),
            bundle_id=bundle_id,
        )


class FakeRuntimeFiles:
    def __init__(self) -> None:
        self.removed: list[Path] = []

    def remove(self, root: Path) -> None:
        self.removed.append(root)


@dataclass
class FakeDeletionStrategy:
    expected_freed_size: int = 1024
    executed: bool = False

    def execute(self) -> None:
        self.executed = True


class FakeModelSupply:
    def __init__(self, cache_root: Path) -> None:
        self.cache_root = cache_root
        self.calls: list[tuple[object, ...]] = []
        self.strategies: list[FakeDeletionStrategy] = []
        self.revisions: tuple[CachedRevision, ...] = ()

    def install(
        self,
        *,
        alias: str,
        repo_id: str,
        revision: str,
        offline: bool = False,
    ) -> ModelInstallResult:
        self.calls.append(("install", alias, repo_id, revision, offline))
        sha = _SHA_A if revision in {"main", _SHA_A} else _SHA_B
        model_revision = ModelRevision(repo_id, sha, revision, "hub-observed")
        snapshot = self.cache_root / sha
        snapshot.mkdir(parents=True, exist_ok=True)
        cached = CachedRevision(
            revision_id=model_revision.revision_id,
            repo_id=repo_id,
            commit_sha=sha,
            snapshot_path=snapshot,
            size_on_disk=17,
            evidence="downloaded-exact",
            complete=True,
        )
        installation = ModelInstallation(
            installation_id=model_revision.revision_id,
            revision=model_revision,
            cached_revision_id=cached.revision_id,
            snapshot_path=snapshot,
            provenance=ModelProvenance(revision, sha, "hugging-face-cache"),
        )
        self.revisions = tuple(
            item for item in self.revisions if item.commit_sha != sha
        ) + (cached,)
        return ModelInstallResult(
            model_revision,
            cached,
            installation,
            ModelAlias(alias, installation.installation_id),
            VerificationResult("complete", "cache-completeness", ()),
        )

    def resolve(
        self, repo_id: str, revision: str, *, offline: bool = False
    ) -> ModelRevision:
        sha = _SHA_A if revision in {"main", _SHA_A} else _SHA_B
        self.calls.append(("resolve", repo_id, revision, offline))
        return ModelRevision(repo_id, sha, revision, "test-resolution")

    def repair(self, installation: ModelInstallation) -> VerificationResult:
        self.calls.append(("repair", installation))
        return VerificationResult("complete", "cache-completeness", ())

    def verify(self, installation: ModelInstallation) -> VerificationResult:
        self.calls.append(("verify", installation))
        return VerificationResult("complete", "cache-completeness", ())

    def search(self, query: str, *, mode: str = "curated", limit: int = 20):
        self.calls.append(("search", query, mode, limit))
        return (query, mode, limit)

    def inventory(self) -> CacheInventory:
        self.calls.append(("inventory",))
        return CacheInventory(self.revisions, "local-observed", ())

    def plan_cache_deletion(
        self,
        commit_hashes: tuple[str, ...],
        *,
        installations: tuple[ModelInstallation, ...] = (),
    ) -> CacheDeletionPlan:
        self.calls.append(("plan_cache_deletion", commit_hashes, installations))
        blocked = tuple(
            installation.installation_id
            for installation in installations
            if installation.revision.commit_sha in commit_hashes
        )
        if blocked:
            return CacheDeletionPlan(False, commit_hashes, blocked, 0)
        strategy = FakeDeletionStrategy()
        self.strategies.append(strategy)
        return CacheDeletionPlan(
            True,
            commit_hashes,
            (),
            strategy.expected_freed_size,
            strategy,
        )


class FakeCacheMover:
    def __init__(self) -> None:
        self.plans: list[CacheMovePlan] = []
        self.executed: list[CacheMovePlan] = []

    def plan(self, revision: CachedRevision, destination: Path) -> CacheMovePlan:
        plan = CacheMovePlan(
            revision_id=revision.revision_id,
            source=revision.snapshot_path,
            destination=destination,
            bytes_to_copy=revision.size_on_disk,
            steps=("copy", "verify", "publish"),
        )
        self.plans.append(plan)
        return plan

    def execute(self, plan: CacheMovePlan) -> Path:
        self.executed.append(plan)
        return plan.destination


class FakeModelIntelligence:
    def __init__(
        self,
        *signals: TrustSignal,
        compatibility: tuple[RuntimeCompatibility, ...] = (),
        repository_files: tuple[RepositoryFile, ...] = (),
    ) -> None:
        self.signals = signals or (
            TrustSignal(
                "hub_security_scan",
                "info",
                EvidenceState.OBSERVED,
                "Hub security status@test",
                "Hub scans completed with no reported file issues",
            ),
        )
        self.compatibility = compatibility
        self.repository_files = repository_files

    def inspect(self, repository: str, revision: str, **_options):
        return SimpleNamespace(
            identity=SimpleNamespace(repo_id=repository, commit_sha=revision),
            trust_signals=self.signals,
            compatibility=self.compatibility,
            repository_files=self.repository_files,
        )


class TestSupplyPort:
    @pytest.fixture(autouse=True)
    def _setup(self, cleanup: ExitStack) -> None:
        self.root = Path(cleanup.enter_context(TemporaryDirectory()))
        self.store = ConfigStore(self.root / "config.toml", validate_config)
        self.security_state = OperationalStateStore(self.root / "state.sqlite3")
        self.security = ExactRevisionModelSecurity(
            cast(ModelIntelligence, FakeModelIntelligence()), self.security_state
        )
        self.store.import_text(
            """schema_version = 1

[gateway]
host = "127.0.0.1"
port = 8766

[runtimes]
[models]
[aliases]
[services]
[clients]
"""
        )

    def _mark_owned(
        self, port: RuntimeSupplyPort, installation: RuntimeInstallation
    ) -> None:
        installation.root.mkdir(parents=True, exist_ok=True)
        port.record_managed_installation(installation)

    def test_runtime_install_uses_tested_bundle_and_persists_exact_probe(self) -> None:
        manager = FakeRuntimeManager(self.root / "runtimes")
        catalogue = RuntimeCatalogue.load_builtin()
        bundle = next(
            item for item in catalogue.tested_bundles if item.runtime == "optiq"
        )
        port = RuntimeSupplyPort(
            cast(RuntimeManager, manager),
            self.store,
            self.root / "runtimes",
            catalogue=catalogue,
        )

        result = port.execute(
            "runtime.install",
            {
                "name": "optiq",
                "channel": "tested",
                "expected_version": bundle.version,
                "expected_lock_digest": bundle.lock_sha256,
            },
        )

        assert isinstance(result["installation_id"], str)
        installed = self.store.load().value.runtimes[result["installation_id"]]
        assert manager.calls[0][0] == "install_tested"
        assert installed.definition == "optiq"
        assert installed.root == result["root"]
        assert isinstance(result["launcher"], (list, tuple, Mapping, str))
        assert installed.launcher == tuple(result["launcher"])
        assert isinstance(result["capabilities"], (list, tuple, Mapping, str))
        assert installed.capabilities == frozenset(result["capabilities"])
        assert installed.bundle_id == result["bundle_id"]
        assert result["lock_sha256"] == bundle.lock_sha256
        assert isinstance(result["plan"], Mapping)
        assert result["plan"]["operation"] == "install"

    def test_runtime_install_initializes_supported_v1_desired_state(self) -> None:
        store = ConfigStore(self.root / "fresh.toml", validate_config)
        manager = FakeRuntimeManager(self.root / "runtimes")
        port = RuntimeSupplyPort(
            cast(RuntimeManager, manager), store, self.root / "runtimes"
        )

        result = port.execute("runtime.install", {"runtime": "mlx_lm"})

        assert result["installation_id"] in store.load().value.runtimes

    def test_runtime_update_is_side_by_side_and_switches_service_references(
        self,
    ) -> None:
        manager = FakeRuntimeManager(self.root / "runtimes")
        old = manager._installation("optiq-old", "optiq", "0.2", "tested")
        RuntimeSupplyPort.persist_runtime(self.store, old)
        # Build valid dependent desired state in one atomic import.
        self.store.import_text(
            f"""schema_version = 1
[gateway]
host = "127.0.0.1"
port = 8766
[runtimes.optiq-old]
definition = "optiq"
version = "0.2"
provenance = "tested"
root = "{old.root}"
launcher = ["{old.launcher[0]}", "serve"]
capabilities = ["host", "model", "port"]
[models.qwen]
repository = "mlx-community/Qwen"
revision = "{_SHA_A}"
[aliases.coding]
installation = "qwen"
[services.coding]
model_alias = "coding"
runtime = "optiq-old"
route = "coding"
[clients]
"""
        )
        port = RuntimeSupplyPort(
            cast(RuntimeManager, manager), self.store, self.root / "runtimes"
        )

        result = port.execute(
            "runtime.update",
            {"resource": "optiq-old", "version": "0.3", "python": "3.13"},
        )

        config = self.store.load().value
        assert "optiq-old" in config.runtimes
        assert "optiq-0.3-custom" in config.runtimes
        assert config.services["coding"].runtime_installation == "optiq-0.3-custom"
        assert isinstance(result["plan"], Mapping)
        assert result["plan"]["referenced_services"] == ["coding"]

    def test_runtime_update_honors_explicit_channel_contract(self) -> None:
        manager = FakeRuntimeManager(self.root / "runtimes")
        old = manager._installation("optiq-old", "optiq", "0.2", "tested")
        RuntimeSupplyPort.persist_runtime(self.store, old)
        port = RuntimeSupplyPort(
            cast(RuntimeManager, manager), self.store, self.root / "runtimes"
        )

        with pytest.raises(SupplyPortError, match="does not accept"):
            port.execute(
                "runtime.update",
                {
                    "resource": "optiq-old",
                    "channel": "tested",
                    "version": "0.3",
                },
            )
        with pytest.raises(SupplyPortError, match="requires an exact version"):
            port.execute(
                "runtime.update",
                {"resource": "optiq-old", "channel": "custom"},
            )

    def test_runtime_update_and_rollback_reject_incompatible_service_options(
        self, subtests: pytest.Subtests
    ):
        manager = FakeRuntimeManager(self.root / "runtimes")
        current = RuntimeInstallation(
            "optiq-current",
            "optiq",
            "0.2",
            "tested",
            self.root / "runtimes/optiq-current",
            (str(self.root / "runtimes/optiq-current/bin/optiq"), "serve"),
            frozenset({"model", "host", "port", "mtp"}),
        )
        target = RuntimeInstallation(
            "optiq-target",
            "optiq",
            "0.3",
            "tested",
            self.root / "runtimes/optiq-target",
            (str(self.root / "runtimes/optiq-target/bin/optiq"), "serve"),
            frozenset({"model", "host", "port"}),
        )
        self.store.import_text(
            f'''schema_version = 1
[gateway]
[runtimes.optiq-current]
definition = "optiq"
version = "0.2"
provenance = "tested"
root = "{current.root}"
launcher = ["{current.launcher[0]}", "serve"]
capabilities = ["host", "model", "mtp", "port"]
[runtimes.optiq-target]
definition = "optiq"
version = "0.3"
provenance = "tested"
root = "{target.root}"
launcher = ["{target.launcher[0]}", "serve"]
capabilities = ["host", "model", "port"]
[models.qwen]
repository = "mlx-community/Qwen"
revision = "{_SHA_A}"
[aliases.coding]
installation = "qwen"
[services.coding]
model_alias = "coding"
runtime = "optiq-current"
route = "coding"
[services.coding.options]
mtp = true
[clients]
'''
        )
        port = RuntimeSupplyPort(
            cast(RuntimeManager, manager), self.store, self.root / "runtimes"
        )

        for operation in ("runtime.update", "runtime.rollback"):
            with subtests.test(operation=operation):
                with pytest.raises(SupplyPortError, match="missing exact capabilities"):
                    port.execute(
                        operation,
                        {"resource": "optiq-current", "target": "optiq-target"},
                    )
                assert (
                    self.store.load().value.services["coding"].runtime_installation
                    == "optiq-current"
                )

    def test_runtime_remove_is_reference_gated_and_requires_confirmation(self) -> None:
        manager = FakeRuntimeManager(self.root / "runtimes")
        files = FakeRuntimeFiles()
        installation = manager._installation("optiq-old", "optiq", "0.2", "tested")
        RuntimeSupplyPort.persist_runtime(self.store, installation)
        port = RuntimeSupplyPort(
            cast(RuntimeManager, manager),
            self.store,
            self.root / "runtimes",
            filesystem=files,
        )
        self._mark_owned(port, installation)

        with pytest.raises(PermissionError, match="confirmation"):
            port.execute("runtime.remove", {"resource": "optiq-old"})

        result = port.execute(
            "runtime.remove", {"resource": "optiq-old", "confirmed": True}
        )

        assert isinstance(result["plan"], Mapping)
        assert result["plan"]["allowed"]
        assert files.removed == [installation.root]
        assert "optiq-old" not in self.store.load().value.runtimes

    def test_runtime_remove_refuses_a_referenced_installation(self) -> None:
        manager = FakeRuntimeManager(self.root / "runtimes")
        files = FakeRuntimeFiles()
        installation = manager._installation("optiq-old", "optiq", "0.2", "tested")
        self.store.import_text(
            f"""schema_version = 1
[gateway]
[runtimes.optiq-old]
definition = "optiq"
version = "0.2"
provenance = "tested"
root = "{installation.root}"
launcher = ["{installation.launcher[0]}", "serve"]
capabilities = []
[models.qwen]
repository = "mlx-community/Qwen"
revision = "{_SHA_A}"
[aliases.coding]
installation = "qwen"
[services.coding]
model_alias = "coding"
runtime = "optiq-old"
route = "coding"
[clients]
"""
        )
        port = RuntimeSupplyPort(
            cast(RuntimeManager, manager),
            self.store,
            self.root / "runtimes",
            filesystem=files,
        )
        self._mark_owned(port, installation)

        with pytest.raises(SupplyPortError, match="coding"):
            port.execute("runtime.remove", {"resource": "optiq-old", "confirmed": True})

        assert files.removed == []
        assert "optiq-old" in self.store.load().value.runtimes

    def test_adopted_runtime_removal_unregisters_without_deleting_external_root(
        self,
    ) -> None:
        manager = FakeRuntimeManager(self.root / "runtimes")
        files = FakeRuntimeFiles()
        external = self.root / "external"
        external.mkdir()
        port = RuntimeSupplyPort(
            cast(RuntimeManager, manager),
            self.store,
            self.root / "runtimes",
            filesystem=files,
        )
        adopted = port.execute(
            "runtime.adopt", {"runtime": "optiq", "path": str(external)}
        )

        port.execute(
            "runtime.remove",
            {"resource": adopted["installation_id"], "confirmed": True},
        )

        assert files.removed == []

    def test_runtime_remove_requires_a_direct_owned_child_and_exact_marker(
        self,
    ) -> None:
        manager = FakeRuntimeManager(self.root / "runtimes")
        files = FakeRuntimeFiles()
        port = RuntimeSupplyPort(
            cast(RuntimeManager, manager),
            self.store,
            self.root / "runtimes",
            filesystem=files,
        )
        outside = manager._installation(
            "optiq-outside", "optiq", "0.2", "tested", root=self.root / "outside"
        )
        outside.root.mkdir()
        RuntimeSupplyPort.persist_runtime(self.store, outside)

        with pytest.raises(SupplyPortError, match="direct managed child"):
            port.execute(
                "runtime.remove",
                {"resource": "optiq-outside", "confirmed": True},
            )

        assert files.removed == []

        direct = manager._installation("optiq-direct", "optiq", "0.3", "tested")
        direct.root.mkdir(parents=True)
        RuntimeSupplyPort.persist_runtime(self.store, direct)
        with pytest.raises(SupplyPortError, match="ownership marker"):
            port.execute(
                "runtime.remove",
                {"resource": "optiq-direct", "confirmed": True},
            )

        assert files.removed == []

    def test_runtime_prune_retains_two_rollback_candidates_per_definition(self) -> None:
        manager = FakeRuntimeManager(self.root / "runtimes")
        files = FakeRuntimeFiles()
        port = RuntimeSupplyPort(
            cast(RuntimeManager, manager),
            self.store,
            self.root / "runtimes",
            filesystem=files,
        )
        installations = [
            manager._installation(f"optiq-{index}", "optiq", f"0.{index}", "custom")
            for index in range(1, 4)
        ]
        for installation in installations:
            RuntimeSupplyPort.persist_runtime(self.store, installation)
            self._mark_owned(port, installation)

        result = port.execute("runtime.prune", {"confirmed": True})

        assert result["removed"] == ["optiq-1"]
        assert files.removed == [installations[0].root]
        assert set(self.store.load().value.runtimes) == {"optiq-2", "optiq-3"}

    def test_model_install_update_and_rollback_preserve_exact_installations(
        self,
    ) -> None:
        supply = FakeModelSupply(self.root / "cache")
        port = ModelSupplyPort(cast(ModelSupply, supply), self.store, self.security)

        installed = port.execute(
            "model.install",
            {
                "repository": "mlx-community/Qwen",
                "revision": "main",
                "alias": "coding",
            },
        )
        updated = port.execute(
            "model.update",
            {"resource": "coding", "revision": "next"},
        )

        config = self.store.load().value
        assert (
            config.aliases["coding"].installation_name == updated["installation_name"]
        )
        assert installed["installation_name"] in config.models
        assert updated["installation_name"] in config.models
        assert config.models[updated["installation_name"]].revision.revision == _SHA_B

        port.execute(
            "model.rollback",
            {
                "resource": "coding",
                "target": installed["installation_name"],
                "confirmed": True,
            },
        )
        assert (
            self.store.load().value.aliases["coding"].installation_name
            == installed["installation_name"]
        )

    def test_model_adopt_verifies_exact_external_bytes_and_never_owns_them(
        self,
    ) -> None:
        snapshot = self.root / "external-snapshot"
        snapshot.mkdir()
        config = b'{"model_type":"qwen"}'
        weights = b"exact external weights"
        (snapshot / "config.json").write_bytes(config)
        (snapshot / "weights.safetensors").write_bytes(weights)
        (snapshot / ".cache/huggingface").mkdir(parents=True)
        (snapshot / ".cache/huggingface/download.json").write_text("{}")
        files = (
            RepositoryFile(
                "config.json",
                len(config),
                lfs_sha256=hashlib.sha256(config).hexdigest(),
            ),
            RepositoryFile(
                "weights.safetensors",
                len(weights),
                lfs_sha256=hashlib.sha256(weights).hexdigest(),
            ),
        )
        security = ExactRevisionModelSecurity(
            cast(ModelIntelligence, FakeModelIntelligence(repository_files=files)),
            self.security_state,
        )
        supply = FakeModelSupply(self.root / "cache")
        port = ModelSupplyPort(cast(ModelSupply, supply), self.store, security)
        observation = inspect_adopted_snapshot(snapshot)

        result = port.execute(
            "model.adopt",
            {
                "repository": "owner/external-model",
                "revision": _SHA_A,
                "path": str(snapshot),
                "alias": "external",
                "snapshot_fingerprint": observation.fingerprint,
            },
        )

        assert isinstance(result["installation_name"], str)
        desired = self.store.load().value.models[result["installation_name"]]
        assert desired.provenance == "adopted"
        assert desired.path == str(snapshot.resolve())
        assert isinstance(result["verification"], Mapping)
        assert result["verification"]["status"] == "verified"
        assert result["provenance"] == "external-adopted"
        assert isinstance(result["installation_name"], str)
        supplied = port._supplied_installation(result["installation_name"])
        assert supplied.snapshot_path == snapshot.resolve()
        assert supplied.provenance.source == "external-adopted"
        with pytest.raises(SupplyPortError, match="externally owned"):
            port.execute("model.repair", {"resource": "external"})
        assert snapshot.exists()
        pruned = port.execute("model.cache.prune", {"confirmed": True})
        assert isinstance(pruned["plan"], Mapping)
        assert pruned["plan"]["revision_hashes"] == []
        assert snapshot.exists()

    def test_model_adopt_rejects_changed_missing_and_unsafe_snapshots(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        snapshot = self.root / "external-snapshot"
        snapshot.mkdir()
        payload = b"safe"
        file = snapshot / "config.json"
        file.write_bytes(payload)
        files = (
            RepositoryFile(
                "config.json",
                len(payload),
                lfs_sha256=hashlib.sha256(payload).hexdigest(),
            ),
        )
        security = ExactRevisionModelSecurity(
            cast(ModelIntelligence, FakeModelIntelligence(repository_files=files)),
            self.security_state,
        )
        port = ModelSupplyPort(
            cast(ModelSupply, FakeModelSupply(self.root / "cache")),
            self.store,
            security,
        )
        fingerprint = inspect_adopted_snapshot(snapshot).fingerprint
        file.write_bytes(b"changed")
        with pytest.raises(SupplyPortError, match="identity changed"):
            port.execute(
                "model.adopt",
                {
                    "repository": "owner/model",
                    "revision": _SHA_A,
                    "path": str(snapshot),
                    "snapshot_fingerprint": fingerprint,
                },
            )
        file.unlink()
        file.symlink_to(snapshot / "missing")
        with pytest.raises(SupplyPortError, match="symlinks"):
            inspect_adopted_snapshot(snapshot)
        file.unlink()
        file.write_bytes(payload)
        with monkeypatch.context() as patch:
            patch.setattr(
                "mlxctl.infrastructure.supply_ports.os.getuid", lambda: 999999
            )
            with pytest.raises(SupplyPortError, match="owned"):
                inspect_adopted_snapshot(snapshot)
        (snapshot / "unexpected.txt").write_text("not in the exact manifest")
        fingerprint = inspect_adopted_snapshot(snapshot).fingerprint
        with pytest.raises(SupplyPortError, match="integrity_mismatch"):
            port.execute(
                "model.adopt",
                {
                    "repository": "owner/model",
                    "revision": _SHA_A,
                    "path": str(snapshot),
                    "snapshot_fingerprint": fingerprint,
                },
            )

    def test_adopted_snapshot_requires_valid_content_digest_for_every_file(
        self, subtests: pytest.Subtests
    ) -> None:
        snapshot = self.root / "external-snapshot"
        snapshot.mkdir()
        (snapshot / "weights.bin").write_bytes(b"same")

        for evidence in (
            {"path": "weights.bin", "size": 4},
            {"path": "weights.bin", "size": 4, "lfs_sha256": "not-a-digest"},
            {"path": "weights.bin", "size": 4, "blob_id": "not-a-digest"},
        ):
            with (
                subtests.test(evidence=evidence),
                pytest.raises(ModelSecurityPolicyError, match="digest"),
            ):
                verify_adopted_snapshot(snapshot, {"repository_files": [evidence]})

    def test_model_adopt_rejects_mlxctl_owned_and_cache_overlapping_paths(
        self,
    ) -> None:
        owned_root = self.root / "mlxctl-data"
        owned_snapshot = owned_root / "models" / "snapshot"
        owned_snapshot.mkdir(parents=True)
        (owned_snapshot / "weights.bin").write_bytes(b"owned")
        with pytest.raises(SupplyPortError, match="mlxctl-owned"):
            inspect_adopted_snapshot(owned_snapshot, forbidden_roots=(owned_root,))

        cache_snapshot = self.root / "cache" / _SHA_A
        cache_snapshot.mkdir(parents=True)
        (cache_snapshot / "weights.bin").write_bytes(b"cached")
        supply = FakeModelSupply(self.root / "cache")
        supply.revisions = (
            CachedRevision(
                "owner/model@" + _SHA_A,
                "owner/model",
                _SHA_A,
                cache_snapshot,
                6,
                "local-observed",
                True,
            ),
        )
        port = ModelSupplyPort(cast(ModelSupply, supply), self.store, self.security)
        with pytest.raises(SupplyPortError, match="managed Hugging Face cache"):
            port.inspect_adoption(str(cache_snapshot))

    def test_optiq_safetensors_install_persists_launchable_exact_security_evidence(
        self,
    ) -> None:
        supply = FakeModelSupply(self.root / "cache")
        port = ModelSupplyPort(cast(ModelSupply, supply), self.store, self.security)

        result = port.execute(
            "model.install",
            {
                "repository": "mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit",
                "revision": _SHA_A,
                "alias": "qwen-optiq",
            },
        )

        assert isinstance(result["security"], Mapping)
        assert result["security"]["hard_blockers"] == []
        assert result["security"]["verification"]["status"] == "complete"
        persisted = self.security.require(
            "mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit", _SHA_A
        )
        assert persisted["revision"] == _SHA_A

    def test_model_mutation_returns_bounded_security_summary(self) -> None:
        files = tuple(
            RepositoryFile(
                f"{'nested-' * 30}{index:05}.bin",
                1,
                blob_id=hashlib.sha1(str(index).encode()).hexdigest(),
            )
            for index in range(5_000)
        )
        security = ExactRevisionModelSecurity(
            cast(ModelIntelligence, FakeModelIntelligence(repository_files=files)),
            self.security_state,
        )
        port = ModelSupplyPort(
            cast(ModelSupply, FakeModelSupply(self.root / "cache")),
            self.store,
            security,
        )

        result = port.execute(
            "model.install",
            {
                "repository": "owner/large-manifest",
                "revision": _SHA_A,
                "alias": "large",
            },
        )

        encoded = json.dumps(result, separators=(",", ":")).encode()
        assert len(encoded) < MAX_FRAME_BYTES
        assert isinstance(result["security"], Mapping)
        assert result["security"]["repository_file_count"] == 5_000
        assert isinstance(result["security"], (list, tuple, Mapping, str))
        assert "repository_files" not in result["security"]

    def test_model_install_hard_blocks_findings_unsafe_serialization_and_unknown_scan(
        self, subtests: pytest.Subtests
    ) -> None:
        scenarios = (
            TrustSignal(
                "hub_security_scan",
                "danger",
                EvidenceState.CONFLICTING,
                "Hub security status@test",
                "infected pickle",
            ),
            TrustSignal(
                "unsafe_serialization",
                "warning",
                EvidenceState.OBSERVED,
                "Hub repository inventory@test",
                "weights.bin",
            ),
            TrustSignal(
                "hub_security_scan",
                "unknown",
                EvidenceState.UNKNOWN,
                "Hub security status@test",
                "scan unavailable",
            ),
        )
        for signal in scenarios:
            with subtests.test(signal=signal.name, severity=signal.severity):
                supply = FakeModelSupply(self.root / f"cache-{signal.severity}")
                security = ExactRevisionModelSecurity(
                    cast(ModelIntelligence, FakeModelIntelligence(signal)),
                    self.security_state,
                )
                port = ModelSupplyPort(cast(ModelSupply, supply), self.store, security)

                with pytest.raises(SupplyPortError, match="security policy"):
                    port.execute(
                        "model.install",
                        {
                            "repository": "owner/unsafe-model",
                            "revision": _SHA_A,
                        },
                    )

                assert not any(call[0] == "install" for call in supply.calls)

    def test_integrity_mismatch_is_persisted_and_cannot_be_granted(self) -> None:
        assessment = self.security.inspect("owner/model", _SHA_A)

        with pytest.raises(SupplyPortError, match="integrity_mismatch"):
            self.security.record_verification(
                assessment,
                VerificationResult("incomplete", "cache-check", ("hash mismatch",)),
            )

        with pytest.raises(SupplyPortError, match="integrity_mismatch"):
            self.security.require("owner/model", _SHA_A)

    def test_model_update_and_rollback_block_explicit_runtime_incompatibility(
        self, subtests: pytest.Subtests
    ):
        supply = FakeModelSupply(self.root / "cache")
        supply.install(alias="coding", repo_id="mlx-community/Qwen", revision=_SHA_A)
        supply.install(alias="coding", repo_id="mlx-community/Qwen", revision=_SHA_B)
        self.store.import_text(
            f'''schema_version = 1
[gateway]
[runtimes.optiq]
definition = "optiq"
version = "0.3"
provenance = "tested"
root = "{self.root / "runtimes/optiq"}"
launcher = ["{self.root / "runtimes/optiq/bin/optiq"}", "serve"]
capabilities = ["host", "model", "port"]
[models.qwen-old]
repository = "mlx-community/Qwen"
revision = "{_SHA_A}"
[models.qwen-target]
repository = "mlx-community/Qwen"
revision = "{_SHA_B}"
[aliases.coding]
installation = "qwen-old"
[services.coding]
model_alias = "coding"
runtime = "optiq"
route = "coding"
[clients]
'''
        )
        compatibility = RuntimeCompatibility(
            "optiq",
            "optiq",
            "0.3",
            "unsupported",
            frozenset({"model", "host", "port"}),
            "exact runtime and model metadata",
            "explicit architecture contradiction",
        )
        security = ExactRevisionModelSecurity(
            cast(
                ModelIntelligence, FakeModelIntelligence(compatibility=(compatibility,))
            ),
            self.security_state,
        )
        port = ModelSupplyPort(cast(ModelSupply, supply), self.store, security)

        requests = (
            ("model.update", {"resource": "coding", "revision": _SHA_B}),
            (
                "model.rollback",
                {
                    "resource": "coding",
                    "target": "qwen-target",
                    "confirmed": True,
                },
            ),
        )
        for operation, parameters in requests:
            with subtests.test(operation=operation):
                with pytest.raises(SupplyPortError, match="explicitly unsupported"):
                    port.execute(operation, parameters)
                assert (
                    self.store.load().value.aliases["coding"].installation_name
                    == "qwen-old"
                )

    def test_model_repair_delegates_the_exact_pinned_revision(self) -> None:
        supply = FakeModelSupply(self.root / "cache")
        port = ModelSupplyPort(cast(ModelSupply, supply), self.store, self.security)
        installed = port.execute(
            "model.install",
            {
                "repository": "mlx-community/Qwen",
                "revision": _SHA_A,
                "alias": "coding",
            },
        )

        result = port.execute(
            "model.repair", {"resource": installed["installation_name"]}
        )

        repaired = supply.calls[-1][1]
        assert isinstance(repaired, ModelInstallation)
        assert repaired.revision.commit_sha == _SHA_A
        assert isinstance(result["verification"], Mapping)
        assert result["verification"]["status"] == "complete"

    def test_cache_eviction_is_reference_aware_and_requires_confirmation(self) -> None:
        supply = FakeModelSupply(self.root / "cache")
        port = ModelSupplyPort(cast(ModelSupply, supply), self.store, self.security)
        installed = port.execute(
            "model.install",
            {
                "repository": "mlx-community/Qwen",
                "revision": _SHA_A,
                "alias": "coding",
            },
        )

        with pytest.raises(SupplyPortError, match="referenced"):
            port.execute(
                "model.cache.evict",
                {"resource": _SHA_A, "confirmed": True},
            )

        self.store.edit(
            lambda document: (
                document["aliases"].pop("coding"),
                document["models"].pop(installed["installation_name"]),
            )
        )
        with pytest.raises(PermissionError, match="confirmation"):
            port.execute("model.cache.evict", {"resource": _SHA_A})

        result = port.execute(
            "model.cache.evict", {"resource": _SHA_A, "confirmed": True}
        )
        assert isinstance(result["plan"], Mapping)
        assert result["plan"]["allowed"]
        assert supply.strategies[-1].executed

    def test_cache_prune_deletes_only_unreferenced_revisions(self) -> None:
        supply = FakeModelSupply(self.root / "cache")
        port = ModelSupplyPort(cast(ModelSupply, supply), self.store, self.security)
        port.execute(
            "model.install",
            {
                "repository": "mlx-community/Qwen",
                "revision": _SHA_A,
                "alias": "coding",
            },
        )
        unreferenced = CachedRevision(
            "other/model@" + _SHA_B,
            "other/model",
            _SHA_B,
            self.root / "cache" / _SHA_B,
            33,
            "local-observed",
            True,
        )
        supply.revisions += (unreferenced,)

        result = port.execute("model.cache.prune", {"confirmed": True})

        assert isinstance(result["plan"], Mapping)
        assert result["plan"]["revision_hashes"] == [_SHA_B]
        assert supply.strategies[-1].executed

    def test_cache_move_exposes_plan_and_confirms_source_cleanup(self) -> None:
        supply = FakeModelSupply(self.root / "cache")
        mover = FakeCacheMover()
        port = ModelSupplyPort(
            cast(ModelSupply, supply), self.store, self.security, cache_mover=mover
        )
        port.execute(
            "model.install",
            {
                "repository": "mlx-community/Qwen",
                "revision": _SHA_A,
                "alias": "coding",
            },
        )

        with pytest.raises(PermissionError, match="confirmation"):
            port.execute(
                "model.cache.move",
                {
                    "resource": _SHA_A,
                    "destination": str(self.root / "new-cache"),
                    "cleanup_source": True,
                },
            )

        result = port.execute(
            "model.cache.move",
            {
                "resource": _SHA_A,
                "destination": str(self.root / "new-cache"),
                "cleanup_source": True,
                "confirmed": True,
            },
        )
        assert isinstance(result["plan"], Mapping)
        assert result["plan"]["bytes_to_copy"] == 17
        assert len(mover.executed) == 1
        assert mover.executed[0].cleanup_source

    def test_default_cache_mover_content_verifies_before_atomic_publish(self) -> None:
        source = self.root / "source"
        source.mkdir()
        (source / "weights.bin").write_bytes(b"exact model bytes")
        revision = CachedRevision(
            "mlx-community/Qwen@" + _SHA_A,
            "mlx-community/Qwen",
            _SHA_A,
            source,
            17,
            "local-observed",
            True,
        )
        destination = self.root / "destination"
        mover = VerifiedCacheMover()

        published = mover.execute(mover.plan(revision, destination))

        assert published == destination.resolve()
        assert (published / "weights.bin").read_bytes() == b"exact model bytes"
        assert source.exists()
