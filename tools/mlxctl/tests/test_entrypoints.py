import shutil
import subprocess


class TestEntrypoint:
    def test_installed_cli_script_has_help(self) -> None:
        executable = shutil.which("mlxctl")
        assert executable is not None
        result = subprocess.run(
            [executable, "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

        assert result.returncode == 0, result.stderr
        assert "usage: mlxctl" in result.stdout

    def test_installed_daemon_script_has_help(self) -> None:
        executable = shutil.which("mlxd")
        assert executable is not None
        result = subprocess.run(
            [executable, "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

        assert result.returncode == 0, result.stderr
        assert "usage: mlxd" in result.stdout

    def test_status_help_describes_the_status_surface_without_a_server_argument(
        self,
    ) -> None:
        executable = shutil.which("mlxctl")
        assert executable is not None
        result = subprocess.run(
            [executable, "status", "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

        assert result.returncode == 0, result.stderr
        assert "Supervisor, Gateway, Inference Services" in result.stdout
        assert "SERVER" not in result.stdout
