from __future__ import annotations

import os
import plistlib
import stat
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypedDict

import pytest

from mlxctl.infrastructure.launchd import (
    CommandResult,
    CommandRunner,
    LaunchdAdapter,
    LaunchdConfigurationError,
)


class LaunchdArguments(TypedDict):
    label: str
    program_arguments: Sequence[str]
    plist_path: Path | str
    runner: CommandRunner
    uid: int


class LaunchdOverrides(TypedDict, total=False):
    label: str
    program_arguments: Sequence[str]
    plist_path: Path | str


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.results: list[CommandResult] = []

    def run(self, argv):
        self.calls.append(tuple(argv))
        if self.results:
            return self.results.pop(0)
        return CommandResult(0, "", "")


class TestLaunchdAdapter:
    @pytest.fixture(autouse=True)
    def _setup(self, cleanup: ExitStack) -> None:
        self.root = Path(cleanup.enter_context(TemporaryDirectory()))
        self.plist = self.root / "Library" / "LaunchAgents" / "com.nisavid.mlxd.plist"
        self.runner = FakeRunner()
        self.adapter = LaunchdAdapter(
            label="com.nisavid.mlxd",
            program_arguments=("/Users/example/.local/bin/mlxd", "serve"),
            plist_path=self.plist,
            runner=self.runner,
            uid=os.getuid(),
        )

    def test_preview_is_an_inactive_per_user_launch_agent(self) -> None:
        preview = plistlib.loads(self.adapter.preview())

        assert preview["Label"] == "com.nisavid.mlxd"
        assert preview["ProgramArguments"] == [
            "/Users/example/.local/bin/mlxd",
            "serve",
        ]
        assert not preview["RunAtLoad"]
        assert not preview["KeepAlive"]
        assert preview["ProcessType"] == "Background"
        assert "Program" not in preview
        assert "ShellPath" not in preview

    def test_register_writes_private_owned_plist_and_does_not_start_service(self):
        status = self.adapter.register()

        assert status.registered
        assert not status.running
        assert self.runner.calls == [
            ("launchctl", "bootstrap", f"gui/{os.getuid()}", str(self.plist))
        ]
        assert stat.S_IMODE(self.plist.stat().st_mode) == 0o600
        assert self.plist.stat().st_uid == os.getuid()
        assert plistlib.loads(self.plist.read_bytes())["RunAtLoad"] == False

    def test_kickstart_bootout_and_status_use_exact_safe_targets(self) -> None:
        self.adapter.kickstart()
        self.adapter.bootout()
        self.runner.results.append(CommandResult(0, "state = running\npid = 123\n", ""))
        status = self.adapter.status()

        target = f"gui/{os.getuid()}/com.nisavid.mlxd"
        assert self.runner.calls == [
            ("launchctl", "kickstart", target),
            ("launchctl", "bootout", target),
            ("launchctl", "print", target),
        ]
        assert status.registered
        assert status.running
        assert status.pid == 123

    def test_unregistered_status_is_observed_without_mutation(self) -> None:
        self.runner.results.append(CommandResult(113, "", "Could not find service"))

        status = self.adapter.status()

        assert not status.registered
        assert not status.running
        assert len(self.runner.calls) == 1

    def test_rejects_unsafe_label_argv_and_plist_targets(
        self, subtests: pytest.Subtests
    ) -> None:
        cases: tuple[LaunchdOverrides, ...] = (
            {"label": "bad/label"},
            {"label": "mlxd"},
            {"program_arguments": ("mlxd",)},
            {"program_arguments": ("/bin/mlxd\x00oops",)},
            {"plist_path": self.root / "wrong-name.plist"},
            {"plist_path": Path("com.nisavid.mlxd.plist")},
        )
        defaults: LaunchdArguments = {
            "label": "com.nisavid.mlxd",
            "program_arguments": ("/usr/local/bin/mlxd",),
            "plist_path": self.plist,
            "runner": self.runner,
            "uid": os.getuid(),
        }
        for overrides in cases:
            with (
                subtests.test(overrides=overrides),
                pytest.raises(LaunchdConfigurationError),
            ):
                arguments: LaunchdArguments = {**defaults, **overrides}
                LaunchdAdapter(**arguments)

    def test_refuses_to_replace_a_symlink_or_foreign_owned_file(self) -> None:
        self.plist.parent.mkdir(parents=True)
        target = self.root / "elsewhere"
        target.write_text("do not replace", encoding="utf-8")
        self.plist.symlink_to(target)
        with pytest.raises(LaunchdConfigurationError, match="symbolic link"):
            self.adapter.install()
        assert target.read_text(encoding="utf-8") == "do not replace"

    def test_refuses_a_symlinked_launch_agents_directory(self) -> None:
        real_directory = self.root / "real-agents"
        real_directory.mkdir()
        self.plist.parent.parent.mkdir(parents=True)
        self.plist.parent.symlink_to(real_directory)

        with pytest.raises(LaunchdConfigurationError, match="symbolic link"):
            self.adapter.install()
