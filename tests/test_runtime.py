"""Runtime portability checks; no SDK, LLM, tree-sitter or container needed."""
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from harmony_bench.paths import repo_root, toolchain_root

runner = importlib.import_module("harmony_bench.runner.type_check")


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hb runtime ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root / "material source"
        self.tc = self.root / "linux toolchain"
        (self.repo / "hvigor").mkdir(parents=True)
        self.tc.mkdir()
        self.cfg = self.repo / "hvigor" / "hvigor-config.json5"
        self.cfg.write_text('{"execution": {\n}}', encoding="utf-8")
        self.env = patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.which = patch.object(runner.shutil, "which", return_value="/usr/bin/udocker")
        self.which.start()
        self.addCleanup(self.which.stop)

    def run_oracle(self, **kwargs):
        return runner.type_check("mine", str(self.repo), str(self.tc), **kwargs)

    @patch.object(runner.subprocess, "run")
    def test_separate_mounts_spaces_and_persistent_repository(self, run):
        run.return_value = SimpleNamespace(stdout="HB_VERDICT=0\n")
        os.environ.update(UDOCKER_DIR=str(self.root / "persistent containers"), HB_CONTAINER="custom")
        self.assertEqual(self.run_oracle().verdict, runner.Verdict.GREEN)
        cmd = run.call_args.args[0]
        for path in (str(self.repo), str(self.tc), str(Path(runner._SCRIPT).resolve().parent)):
            self.assertIn(f"{path}:{path}", cmd)
        self.assertEqual(cmd[-3], "custom")
        self.assertEqual(run.call_args.kwargs["env"]["UDOCKER_DIR"], os.environ["UDOCKER_DIR"])
        self.assertFalse(any(x.startswith("--env=HB_PROXY=") for x in cmd))

    @patch.object(runner.subprocess, "run")
    def test_explicit_options_override_environment(self, run):
        os.environ.update(HB_CONTAINER_ENGINE="docker", HB_CONTAINER="other", UDOCKER_DIR="other", HB_PROXY="http://proxy")
        run.return_value = SimpleNamespace(stdout="HB_VERDICT=1\nHB_LOG_TAIL_BEGIN\nerror detail\nHB_LOG_TAIL_END\n")
        result = self.run_oracle(container="explicit", udocker_dir=str(self.root), proxy="")
        self.assertEqual(result.verdict, runner.Verdict.RED)
        self.assertEqual(result.log_tail, "error detail")
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[0], "docker")
        self.assertIn("--rm", cmd)
        self.assertEqual(cmd[-3], "explicit")
        self.assertEqual(run.call_args.kwargs["env"]["UDOCKER_DIR"], str(self.root))
        self.assertFalse(any(x.startswith("--env=HB_PROXY=") for x in cmd))

    @patch.object(runner.subprocess, "run")
    def test_proxy_is_read_at_call_time(self, run):
        os.environ["HB_PROXY"] = "http://localhost:8080"
        run.return_value = SimpleNamespace(stdout="HB_VERDICT=0\n")
        self.run_oracle()
        self.assertIn("--env=HB_PROXY=http://localhost:8080", run.call_args.args[0])

    def test_missing_engine_does_not_modify_config(self):
        original = self.cfg.read_bytes()
        with patch.object(runner.shutil, "which", return_value=None):
            result = self.run_oracle()
        self.assertEqual(result.verdict, runner.Verdict.ERROR)
        self.assertIn("not found", result.log_tail)
        self.assertEqual(self.cfg.read_bytes(), original)

    def test_invalid_mount_root(self):
        result = self.run_oracle(mount_root=str(self.repo))
        self.assertEqual(result.verdict, runner.Verdict.ERROR)
        self.assertIn("must contain", result.log_tail)

    @patch.object(runner.subprocess, "run")
    def test_explicit_common_mount_still_maps_runner(self, run):
        run.return_value = SimpleNamespace(stdout="HB_VERDICT=0\n")
        self.run_oracle(mount_root=str(self.root))
        cmd = run.call_args.args[0]
        self.assertIn(f"{self.root}:{self.root}", cmd)
        directory = str(Path(runner._SCRIPT).resolve().parent)
        self.assertIn(f"{directory}:{directory}", cmd)

    def test_missing_config_is_environment_error(self):
        self.cfg.unlink()
        self.assertEqual(self.run_oracle().verdict, runner.Verdict.ERROR)

    @patch.object(runner.subprocess, "run", side_effect=OSError("cannot execute"))
    def test_spawn_failure_is_environment_error(self, run):
        self.assertEqual(self.run_oracle().verdict, runner.Verdict.ERROR)

    @patch.object(runner.subprocess, "run", side_effect=subprocess.TimeoutExpired("udocker", 1))
    def test_timeout_is_environment_error(self, run):
        self.assertEqual(self.run_oracle().verdict, runner.Verdict.ERROR)

    @patch.object(runner.subprocess, "run")
    def test_no_marker_is_environment_error(self, run):
        run.return_value = SimpleNamespace(stdout="container not found")
        self.assertEqual(self.run_oracle().verdict, runner.Verdict.ERROR)

    def test_configured_paths_are_independent_of_cwd(self):
        os.environ.update(HB_REPO_ROOT=str(self.repo), HB_TOOLCHAIN_ROOT=str(self.tc))
        self.assertEqual(repo_root(), str(self.repo))
        self.assertEqual(toolchain_root(), str(self.tc))
        del os.environ["HB_TOOLCHAIN_ROOT"]
        self.assertEqual(toolchain_root(), str(self.root / "harmony_toolchain"))


@unittest.skipUnless(sys.platform == "linux" and shutil.which("bash"), "Linux Bash execution")
class OracleShellTests(unittest.TestCase):
    def check_marker(self, marker, verdict):
        with tempfile.TemporaryDirectory(prefix="hb shell ") as directory:
            root = Path(directory)
            repo, tc = root / "repo", root / "tc"
            module = repo / "features" / "mine"
            module.mkdir(parents=True)
            (module / "oh-package.json5").write_text('{}')
            (module / "build").mkdir()
            bin_dir = tc / "command-line-tools" / "bin"
            bin_dir.mkdir(parents=True)
            fake = bin_dir / "hvigorw"
            fake.write_text(f'#!/bin/bash\nprintf "%s\\n" "{marker}"\n')
            fake.chmod(0o755)
            env = dict(os.environ, HB_REPO=str(repo), HB_TC=str(tc), HB_MODULE="mine", HB_TIMEOUT="4", HB_PROXY="")
            result = subprocess.run(["bash", runner._SCRIPT], env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, verdict, result.stdout + result.stderr)
            self.assertIn(f"HB_VERDICT={verdict}", result.stdout)
            self.assertFalse((module / "build").exists())

    def test_green_nested_module(self):
        self.check_marker("Finished :mine:default@UnitTestArkTS", 0)

    def test_red_nested_module(self):
        self.check_marker("Failed :mine:default@UnitTestArkTS", 1)

    def test_setup_failure(self):
        self.check_marker("setup failed before compilation", 2)


if __name__ == "__main__":
    unittest.main()
