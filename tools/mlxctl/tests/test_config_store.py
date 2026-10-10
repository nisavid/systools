import shutil
import stat
import tempfile
import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest

from mlxctl.infrastructure.config_store import ConfigChange, ConfigStore


class TestConfigStore:
    def test_rejects_symlinked_config_history_lock_and_journal_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.write_text("preserve", encoding="utf-8")

            config = root / "config.toml"
            config.symlink_to(outside)
            with pytest.raises(OSError):
                ConfigStore(config, lambda data: data)
            config.unlink()

            history = root / ".config.toml.history"
            shutil.rmtree(history)
            history.symlink_to(root, target_is_directory=True)
            with pytest.raises(RuntimeError):
                ConfigStore(config, lambda data: data)
            history.unlink()

            store = ConfigStore(config, lambda data: data)
            lock = root / "config.toml.lock"
            lock.symlink_to(outside)
            with pytest.raises(OSError):
                store.import_text("schema_version = 1\n")
            lock.unlink()

            journal = root / ".config.toml.journal.jsonl"
            journal.symlink_to(outside)
            with pytest.raises(OSError):
                store.import_text("schema_version = 1\n")

            assert outside.read_text(encoding="utf-8") == "preserve"

    def test_rejects_a_private_directory_not_owned_by_the_current_user(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            monkeypatch.context() as patched,
        ):
            patched.setattr(
                "mlxctl.infrastructure.config_store.os.getuid",
                lambda: Path(directory).stat().st_uid + 1,
            )
            with pytest.raises(PermissionError):
                ConfigStore(Path(directory) / "config.toml", lambda data: data)

    def test_exists_distinguishes_uninitialized_from_saved_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory) / "config.toml", lambda data: data)

            assert not store.exists
            store.import_text("schema_version = 1\n")
            assert store.exists

    def test_round_trips_comments_and_returns_validated_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mlxctl" / "config.toml"
            store = ConfigStore(path, lambda data: cast(int, data["schema_version"]))

            saved = store.import_text(
                "# operator note\nschema_version = 1\n\n[gateway]\nport = 8766\n"
            )
            saved.document["gateway"]["port"] = 9000
            loaded = store.save(saved.document)

            assert loaded.value == 1
            assert "# operator note" in store.export_text()
            assert "port = 9000" in store.export_text()
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    def test_failed_validation_does_not_replace_the_current_document(self) -> None:
        def validate(data: Mapping[str, object]) -> int:
            version = cast(int, data["schema_version"])
            if version != 1:
                raise ValueError("unsupported schema")
            return version

        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory) / "config.toml", validate)
            store.import_text("schema_version = 1\n")

            with pytest.raises(ValueError, match="unsupported schema"):
                store.import_text("schema_version = 2\n")

            assert store.export_text() == "schema_version = 1\n"

    def test_records_semantic_history_and_restores_an_exact_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(
                Path(directory) / "config.toml", lambda data: data["schema_version"]
            )
            first = store.import_text(
                "# first\nschema_version = 1\n[gateway]\nport = 8766\n"
            )
            second = store.import_text(
                "# second\nschema_version = 1\n[gateway]\nport = 9000\n"
            )

            assert store.diff(first.document) == (
                ConfigChange(("gateway", "port"), 9000, 8766),
            )
            assert tuple(item.revision for item in store.history()) == (
                first.revision,
                second.revision,
            )

            restored = store.restore(first.revision)

            assert restored.revision == first.revision
            assert store.export_text() == first.document.as_string()

    def test_serializes_concurrent_semantic_edits_without_losing_updates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(
                Path(directory) / "config.toml", lambda data: cast(int, data["count"])
            )
            store.import_text("count = 0\n")
            ready = threading.Barrier(8)

            def increment() -> None:
                ready.wait()
                store.edit(
                    lambda document: document.__setitem__(
                        "count", document["count"] + 1
                    )
                )

            with ThreadPoolExecutor(max_workers=8) as pool:
                tuple(pool.map(lambda _index: increment(), range(8)))

            assert store.load().value == 8

    def test_recovers_a_replaced_config_when_the_journal_commit_was_interrupted(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            store = ConfigStore(path, lambda data: cast(int, data["generation"]))
            store.import_text("generation = 1\n")
            current = store.import_text("generation = 2\n")
            journal = path.parent / ".config.toml.journal.jsonl"
            entries = journal.read_bytes().splitlines(keepends=True)
            journal.write_bytes(entries[0] + b'{"revision":')

            recovered = ConfigStore(path, lambda data: cast(int, data["generation"]))

            assert recovered.load().value == 2
            assert recovered.history()[-1].revision == current.revision
            assert recovered.history()[-1].action == "recovered"

    def test_reports_a_complete_corrupt_journal_entry_instead_of_discarding_it(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            store = ConfigStore(path, lambda data: cast(int, data["schema_version"]))
            store.import_text("schema_version = 1\n")
            journal = path.parent / ".config.toml.journal.jsonl"
            with journal.open("ab") as stream:
                stream.write(b"not-json\n")

            with pytest.raises(RuntimeError, match="config journal is corrupt"):
                store.history()
