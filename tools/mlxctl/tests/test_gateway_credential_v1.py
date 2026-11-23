from __future__ import annotations

import os
import stat
import tempfile
import threading
from pathlib import Path

import pytest

from mlxctl.infrastructure.gateway_credential import GatewayCredential


class TestGatewayCredential:
    def test_generates_one_private_persistent_token_and_authenticates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            path = root / "gateway.token"
            credential = GatewayCredential(path)

            first = credential.load_or_create()
            second = credential.load_or_create()

            assert first == second
            assert len(first) >= 32
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            assert path.stat().st_uid == os.getuid()
            assert not credential.authenticate(None)
            assert not credential.authenticate("Bearer wrong")
            assert credential.authenticate(f"Bearer {first}")

    def test_rejects_symlink_non_private_and_non_regular_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            target = root / "target"
            target.write_text("keep", encoding="utf-8")
            link = root / "gateway.token"
            link.symlink_to(target)
            with pytest.raises(OSError):
                GatewayCredential(link).load_or_create()
            assert target.read_text(encoding="utf-8") == "keep"

            link.unlink()
            link.mkdir()
            with pytest.raises(PermissionError, match="regular file"):
                GatewayCredential(link).load_or_create()

            link.rmdir()
            link.write_text("a" * 43 + "\n", encoding="utf-8")
            link.chmod(0o644)
            with pytest.raises(PermissionError, match="mode 0600"):
                GatewayCredential(link).load_or_create()

    def test_rejects_unsafe_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            root.mkdir(mode=0o755)
            with pytest.raises(PermissionError, match="mode 0700"):
                GatewayCredential(root / "gateway.token").load_or_create()

    def test_concurrent_creation_converges_on_one_complete_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gateway.token"
            barrier = threading.Barrier(8)
            tokens: list[str] = []

            def create() -> None:
                barrier.wait()
                tokens.append(GatewayCredential(path).load_or_create())

            threads = [threading.Thread(target=create) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(2)

            assert len(tokens) == 8
            assert len(set(tokens)) == 1
            assert tokens[0] == GatewayCredential(path).load_or_create()
