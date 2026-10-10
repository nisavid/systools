import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from mlxctl.infrastructure.model_supply import (
    CachedRevision,
    CacheInventory,
    HubModelRecord,
    HuggingFaceHubClient,
    ModelSupply,
    VerificationResult,
)


@dataclass
class FakeDeletionStrategy:
    expected_freed_size: int
    executed: bool = False

    def execute(self) -> None:
        self.executed = True


class FakeHub:
    def __init__(self, snapshot: Path) -> None:
        self.snapshot = snapshot
        self.search_calls: list[tuple[str, str | None, int]] = []
        self.resolve_calls: list[tuple[str, str, bool]] = []
        self.download_calls: list[tuple[str, str, bool, bool]] = []
        self.verification = VerificationResult(
            status="complete",
            evidence="cache-completeness",
            issues=(),
        )
        self.inventory = CacheInventory(
            revisions=(
                CachedRevision(
                    revision_id="mlx-community/local@abc123",
                    repo_id="mlx-community/local",
                    commit_sha="abc123",
                    snapshot_path=snapshot,
                    size_on_disk=1234,
                    evidence="local-observed",
                    complete=True,
                ),
            ),
            evidence="local-observed",
            warnings=(),
        )
        self.deletion = FakeDeletionStrategy(expected_freed_size=900)
        self.deletion_calls: list[tuple[str, ...]] = []

    def search_models(
        self, query: str, *, author: str | None, limit: int
    ) -> tuple[HubModelRecord, ...]:
        self.search_calls.append((query, author, limit))
        return (
            HubModelRecord(
                repo_id="mlx-community/Qwen-test",
                reported_sha="f" * 40,
                pipeline_tag="text-generation",
                library_name="mlx",
                tags=("mlx", "4-bit"),
                private=False,
                gated=False,
            ),
        )

    def resolve_revision(
        self, repo_id: str, revision: str, *, local_files_only: bool
    ) -> str:
        self.resolve_calls.append((repo_id, revision, local_files_only))
        return "a" * 40

    def snapshot_download(
        self,
        repo_id: str,
        revision: str,
        *,
        local_files_only: bool,
        force_download: bool,
    ) -> Path:
        self.download_calls.append(
            (repo_id, revision, local_files_only, force_download)
        )
        return self.snapshot

    def verify_revision(
        self, repo_id: str, revision: str, snapshot_path: Path
    ) -> VerificationResult:
        return self.verification

    def cache_inventory(self) -> CacheInventory:
        return self.inventory

    def plan_cache_deletion(self, commit_hashes: tuple[str, ...]):
        self.deletion_calls.append(commit_hashes)
        return self.deletion


class TestModelDiscovery:
    def test_curated_search_defaults_to_mlx_community_without_claiming_compatibility(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            hub = FakeHub(Path(directory))
            supply = ModelSupply(hub)

            candidates = supply.search("Qwen", mode="curated", limit=8)

            assert hub.search_calls == [("Qwen", "mlx-community", 8)]
            assert candidates[0].repo_id == "mlx-community/Qwen-test"
            assert candidates[0].evidence == "hub-declared"
            assert candidates[0].compatibility is None

    def test_broad_and_local_search_have_distinct_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            hub = FakeHub(Path(directory))
            supply = ModelSupply(hub)

            broad = supply.search("Qwen", mode="broad")
            local = supply.search("local", mode="local")

            assert hub.search_calls[-1] == ("Qwen", None, 20)
            assert broad[0].source == "hub"
            assert local[0].source == "cache"
            assert local[0].reported_sha == "abc123"
            assert local[0].evidence == "local-observed"


class TestModelInstall:
    def test_install_resolves_a_mutable_reference_then_pins_exact_snapshot(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory)
            hub = FakeHub(snapshot)
            supply = ModelSupply(hub)

            result = supply.install(
                alias="coding",
                repo_id="mlx-community/Qwen-test",
                revision="main",
            )

            assert hub.resolve_calls == [("mlx-community/Qwen-test", "main", False)]
            assert hub.download_calls == [
                ("mlx-community/Qwen-test", "a" * 40, False, False)
            ]
            assert result.revision.commit_sha == "a" * 40
            assert result.cached.complete
            assert result.installation.revision == result.revision
            assert result.installation.cached_revision_id == result.cached.revision_id
            assert result.alias.name == "coding"
            assert result.alias.installation_id == result.installation.installation_id
            assert result.installation != result.cached

    def test_offline_install_labels_exact_local_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            hub = FakeHub(Path(directory))
            result = ModelSupply(hub).install(
                alias="offline",
                repo_id="mlx-community/Qwen-test",
                revision="a" * 40,
                offline=True,
            )

            assert hub.resolve_calls[-1] == (
                "mlx-community/Qwen-test",
                "a" * 40,
                True,
            )
            assert result.revision.evidence == "offline-cached"
            assert result.cached.evidence == "offline-cached"

    def test_verify_and_repair_use_the_exact_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            hub = FakeHub(Path(directory))
            supply = ModelSupply(hub)
            installed = supply.install(
                alias="coding",
                repo_id="mlx-community/Qwen-test",
                revision="main",
            ).installation
            hub.verification = VerificationResult(
                status="incomplete",
                evidence="cache-completeness",
                issues=("missing shard",),
            )

            before = supply.verify(installed)
            repaired = supply.repair(installed)

            assert before.status == "incomplete"
            assert hub.download_calls[-1] == (
                "mlx-community/Qwen-test",
                "a" * 40,
                False,
                False,
            )
            assert repaired.status == "incomplete"


class TestModelCache:
    def test_cache_deletion_is_blocked_while_an_installation_references_revision(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            hub = FakeHub(Path(directory))
            supply = ModelSupply(hub)
            installation = supply.install(
                alias="coding",
                repo_id="mlx-community/Qwen-test",
                revision="main",
            ).installation

            plan = supply.plan_cache_deletion(
                (installation.revision.commit_sha,), installations=(installation,)
            )

            assert not plan.allowed
            assert plan.blocked_by == (installation.installation_id,)
            assert hub.deletion_calls == []

    def test_official_cache_deletion_plan_requires_explicit_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            hub = FakeHub(Path(directory))
            plan = ModelSupply(hub).plan_cache_deletion(("abc123",))

            assert plan.allowed
            assert plan.expected_freed_size == 900
            assert hub.deletion_calls == [("abc123",)]
            with pytest.raises(PermissionError, match="explicit approval"):
                plan.execute()
            plan.execute(approved=True)
            assert hub.deletion.executed


class TestHuggingFaceHubClient:
    def test_official_api_objects_are_normalized_behind_the_adapter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / ("b" * 40)
            snapshot.mkdir()
            calls: list[tuple[str, object]] = []
            deletion = FakeDeletionStrategy(456)

            class FakeApi:
                def list_models(self, **kwargs):
                    calls.append(("list_models", kwargs))
                    return (
                        SimpleNamespace(
                            id="mlx-community/Qwen",
                            sha="a" * 40,
                            pipeline_tag="text-generation",
                            library_name="mlx",
                            tags=["mlx"],
                            private=False,
                            gated="manual",
                        ),
                    )

                def model_info(self, repo_id, **kwargs):
                    calls.append(("model_info", (repo_id, kwargs)))
                    return SimpleNamespace(sha="b" * 40)

            def snapshot_download(**kwargs):
                calls.append(("snapshot_download", kwargs))
                return str(snapshot)

            cache_info = SimpleNamespace(
                warnings=(),
                repos=(
                    SimpleNamespace(
                        repo_id="mlx-community/Qwen",
                        revisions=(
                            SimpleNamespace(
                                commit_hash="b" * 40,
                                snapshot_path=snapshot,
                                size_on_disk=321,
                            ),
                        ),
                    ),
                ),
                delete_revisions=lambda *hashes: (
                    calls.append(("delete_revisions", hashes)) or deletion
                ),
            )
            module = ModuleType("huggingface_hub")
            module.__dict__.update(
                HfApi=FakeApi,
                snapshot_download=snapshot_download,
                scan_cache_dir=lambda: cache_info,
            )

            with monkeypatch.context() as patch:
                patch.setitem(sys.modules, "huggingface_hub", module)
                client = HuggingFaceHubClient()
                records = client.search_models("Qwen", author="mlx-community", limit=3)
                resolved = client.resolve_revision(
                    "mlx-community/Qwen", "main", local_files_only=False
                )
                inventory = client.cache_inventory()
                plan = client.plan_cache_deletion(("b" * 40,))

            assert records[0].repo_id == "mlx-community/Qwen"
            assert records[0].gated == "manual"
            assert resolved == "b" * 40
            assert inventory.revisions[0].size_on_disk == 321
            assert inventory.revisions[0].complete is None
            assert plan is deletion
            assert ("delete_revisions", ("b" * 40,)) in calls
