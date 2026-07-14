import subprocess
import sys
import unittest


class EntrypointTests(unittest.TestCase):
    def test_cli_module_has_help(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "mlxctl.cli", "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: mlxctl", result.stdout)

    def test_daemon_module_has_help(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "mlxctl.daemon", "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: mlxd", result.stdout)


if __name__ == "__main__":
    unittest.main()
