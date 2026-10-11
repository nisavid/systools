import tempfile
from pathlib import Path

from mlxctl.infrastructure.paths_v1 import resolve_paths


class TestPathsV1:
    def test_defaults_are_product_named_and_keep_desired_operational_and_logs_separate(
        self,
    ) -> None:
        paths = resolve_paths(home=Path("/Users/ivan"), environment={})

        assert paths.config_file == Path("/Users/ivan/.config/mlxctl/config.toml")
        assert paths.state_db == Path("/Users/ivan/.local/state/mlxctl/state.sqlite3")
        assert paths.control_socket == Path("/Users/ivan/.local/state/mlxctl/mlxd.sock")
        assert paths.runtime_dir == Path("/Users/ivan/.local/share/mlxctl/runtimes")
        assert paths.log_dir == Path("/Users/ivan/Library/Logs/mlxctl")

    def test_xdg_and_explicit_overrides_are_deterministic(self) -> None:
        environment = {
            "XDG_CONFIG_HOME": "/cfg",
            "XDG_STATE_HOME": "/state",
            "XDG_DATA_HOME": "/data",
            "MLXCTL_LOG_DIR": "/logs",
        }

        paths = resolve_paths(home=Path("/home/user"), environment=environment)

        assert paths.config_dir == Path("/cfg/mlxctl")
        assert paths.state_dir == Path("/state/mlxctl")
        assert paths.data_dir == Path("/data/mlxctl")
        assert paths.log_dir == Path("/logs")

    def test_prepare_creates_private_directories_without_creating_configuration(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = resolve_paths(home=Path(directory), environment={})

            paths.prepare()

            for path in (
                paths.config_dir,
                paths.state_dir,
                paths.data_dir,
                paths.runtime_dir,
                paths.log_dir,
            ):
                assert path.is_dir()
                assert path.stat().st_mode & 0o777 == 0o700
            assert not paths.config_file.exists()
