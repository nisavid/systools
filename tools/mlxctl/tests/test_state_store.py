import stat
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mlxctl.infrastructure.state_store import (
    OperationalStateStore,
    SensitiveContentError,
)


class TestOperationalStateStore:
    def test_rejects_a_state_directory_not_owned_by_the_current_user(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            monkeypatch.context() as patched,
        ):
            patched.setattr(
                "mlxctl.infrastructure.state_store.os.getuid",
                lambda: Path(directory).stat().st_uid + 1,
            )
            with pytest.raises(PermissionError):
                OperationalStateStore(Path(directory) / "state.sqlite3")

    def test_rejects_symlinked_or_non_regular_database_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.write_text("preserve", encoding="utf-8")
            database = root / "state.sqlite3"
            database.symlink_to(outside)

            with pytest.raises(OSError):
                OperationalStateStore(database)

            assert outside.read_text(encoding="utf-8") == "preserve"

    def test_rejects_known_credential_fields_at_any_depth(
        self, subtests: pytest.Subtests
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = OperationalStateStore(Path(directory) / "state.sqlite3")

            for key in (
                "api_key",
                "api_token",
                "authorization",
                "password",
                "access_token",
            ):
                with (
                    subtests.test(key=key),
                    pytest.raises(
                        SensitiveContentError,
                        match="cannot persist credential material",
                    ),
                ):
                    store.put_operation({"id": f"op-{key}", "details": {key: "secret"}})

    def test_persists_operations_progress_events_snapshots_and_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mlxctl" / "state.sqlite3"
            store = OperationalStateStore(path)
            store.put_operation(
                {"id": "op-1", "kind": "model.install", "status": "running"}
            )
            progress = store.append_progress(
                "op-1", {"completed": 2, "total": 10, "unit": "files"}
            )
            event = store.append_event(
                {"operation_id": "op-1", "kind": "checkpoint", "label": "weights"}
            )
            store.put_snapshot(
                {"kind": "service", "id": "code", "state": "ready", "version": 3}
            )
            metric = store.record_metric(
                {"kind": "request", "service": "code", "duration_ms": 125.0}
            )

            reopened = OperationalStateStore(path)

            assert reopened.operation("op-1") == {
                "id": "op-1",
                "kind": "model.install",
                "status": "running",
            }
            assert reopened.progress("op-1") == (progress,)
            assert reopened.events("op-1") == (progress, event)
            assert reopened.snapshot("service", "code") == {
                "id": "code",
                "kind": "service",
                "state": "ready",
                "version": 3,
            }
            assert reopened.metrics("request") == (metric,)
            assert tuple(metric) == ("duration_ms", "kind", "sequence", "service")
            assert reopened.metadata() == {"journal_mode": "wal", "schema_version": 1}
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    def test_concurrent_stores_initialize_and_write_without_losing_records(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            ready = threading.Barrier(8)

            def write(index: int) -> None:
                ready.wait()
                store = OperationalStateStore(path)
                store.put_operation(
                    {"id": f"op-{index:02}", "kind": "probe", "status": "done"}
                )

            with ThreadPoolExecutor(max_workers=8) as pool:
                tuple(pool.map(write, range(8)))

            assert tuple(
                operation["id"]
                for operation in OperationalStateStore(path).operations()
            ) == tuple(f"op-{index:02}" for index in range(8))

    def test_rejects_prompt_or_response_content_at_any_depth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = OperationalStateStore(Path(directory) / "state.sqlite3")

            with pytest.raises(
                SensitiveContentError,
                match="cannot persist inference content at details.prompt",
            ):
                store.put_operation(
                    {"id": "op-secret", "details": {"prompt": "do not store me"}}
                )

            assert store.operation("op-secret") is None

    def test_preserves_versioned_snapshots_and_returns_the_latest_by_default(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = OperationalStateStore(Path(directory) / "state.sqlite3")
            first = store.put_snapshot(
                {"kind": "service", "id": "chat", "state": "starting", "version": 1}
            )
            second = store.put_snapshot(
                {"kind": "service", "id": "chat", "state": "ready", "version": 2}
            )

            assert store.snapshot("service", "chat") == second
            assert store.snapshot("service", "chat", version=1) == first
            assert store.snapshots("service") == (first, second)

            with pytest.raises(ValueError, match="version 1 is immutable"):
                store.put_snapshot(
                    {"kind": "service", "id": "chat", "state": "failed", "version": 1}
                )
