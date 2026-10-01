import pathlib
import subprocess
import sys
import tempfile
import unittest
from typing import Any
from unittest import mock

LIBRARY_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "lib"
sys.path.insert(0, str(LIBRARY_DIR))

import cli_options  # noqa: E402
import command_runner  # noqa: E402
import nix_workflow  # noqa: E402
import nvd  # noqa: E402


class FakeRunner:
    def __init__(
        self,
        *,
        update_returncode=0,
        write_lock_file=True,
        build_returncode=0,
        create_result_link=True,
        nvd_returncode=0,
        diff_output="header one\nheader two\n[U.] package 1 -> 2\n",
        build_target,
    ):
        self.update_returncode = update_returncode
        self.write_lock_file = write_lock_file
        self.build_returncode = build_returncode
        self.create_result_link = create_result_link
        self.nvd_returncode = nvd_returncode
        self.diff_output = diff_output
        self.build_target = pathlib.Path(build_target)
        self.calls = []
        self.console = type("FakeConsole", (), {"logger": mock.Mock()})()

    def run(self, command, **kwargs):
        command = list(command)
        self.calls.append({"command": command, **kwargs})
        if command[0] == "nix" and "update" in command:
            returncode = self.update_returncode
            if returncode == 0 and self.write_lock_file:
                lock_path = pathlib.Path(
                    command[command.index("--output-lock-file") + 1]
                )
                lock_path.write_text("updated lock\n")
            stdout = ""
        elif command[0] == "nix" and "build" in command:
            returncode = self.build_returncode
            if returncode == 0 and self.create_result_link:
                result_link = pathlib.Path(command[command.index("--out-link") + 1])
                result_link.symlink_to(self.build_target)
            stdout = ""
        elif command[0] == "nvd":
            returncode = self.nvd_returncode
            stdout = self.diff_output
        else:
            raise AssertionError(f"unexpected command: {command!r}")

        return subprocess.CompletedProcess(
            args=command,
            returncode=returncode,
            stdout=stdout,
            stderr=None,
        )


class NixWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = pathlib.Path(self.temporary_directory.name)
        self.flake_dir = self.root / "flake source"
        self.flake_dir.mkdir()
        (self.flake_dir / "flake.nix").write_text("{ }")
        (self.flake_dir / "flake.lock").write_text("source lock\n")
        self.workspace_dir = self.root / "workspace"
        self.workspace_dir.mkdir()
        self.current_closure = self.root / "current system"
        self.current_closure.mkdir()
        self.upgraded_closure = self.root / "upgraded system"
        self.upgraded_closure.mkdir()
        self.defaults = cli_options.RuntimeDefaults(
            name="nixos-upgrade-test",
            version="test",
            hostname="test-host",
            default_flake=self.flake_dir,
        )

    def make_workflow(self, *arguments, flake_dir=None, runner_options=None):
        selected_flake = (
            self.flake_dir if flake_dir is None else pathlib.Path(flake_dir)
        )
        options = cli_options.parse_args(
            ["--flake", str(selected_flake), *arguments], self.defaults
        )
        runner_configuration: dict[str, Any] = {"build_target": self.upgraded_closure}
        if runner_options is not None:
            runner_configuration.update(runner_options)
        runner = FakeRunner(**runner_configuration)
        workspace = nix_workflow.NixWorkspace(
            lock_file_path=self.workspace_dir / "flake.lock",
            result_link=self.workspace_dir / "result",
        )
        workflow = nix_workflow.NixWorkflow(
            options,
            runner,
            workspace,
            str(self.current_closure),
        )
        return workflow, runner, workspace

    def test_validate_flake_resolves_and_rejects_invalid_paths(self):
        alias = self.root / "flake alias"
        alias.symlink_to(self.flake_dir, target_is_directory=True)
        workflow, _, _ = self.make_workflow(flake_dir=alias)

        self.assertEqual(workflow.validate_flake(), self.flake_dir.resolve())

        missing = self.root / "missing flake"
        not_directory = self.root / "not a directory"
        not_directory.write_text("file")
        missing_file = self.root / "directory without flake"
        missing_file.mkdir()

        for path, expected_message in (
            (missing, f"{missing}: no such directory"),
            (not_directory, f"{not_directory.resolve()}: no such directory"),
            (missing_file, f"{missing_file.resolve()}: this dir is not a flake"),
        ):
            with self.subTest(path=path):
                invalid_workflow, _, _ = self.make_workflow(flake_dir=path)
                with self.assertRaises(nix_workflow.NixWorkflowError) as raised:
                    invalid_workflow.validate_flake()

                self.assertEqual(raised.exception.message, expected_message)
                self.assertEqual(raised.exception.exit_code, 1)

    def test_update_uses_requested_input_names_and_output_lock(self):
        workflow, runner, workspace = self.make_workflow(
            "--inputs", "nixpkgs", "home-manager"
        )

        flake_dir = workflow.validate_flake()
        workflow.prepare(flake_dir)

        update = runner.calls[0]
        self.assertEqual(
            update["command"],
            [
                "nix",
                "--extra-experimental-features",
                "nix-command flakes",
                "flake",
                "update",
                "nixpkgs",
                "home-manager",
                "--flake",
                str(self.flake_dir.resolve()),
                "--output-lock-file",
                str(workspace.lock_file_path),
            ],
        )
        self.assertIs(
            update["output_policy"], command_runner.OutputPolicy.INHERIT_STDERR
        )
        self.assertEqual(update["description"], "updating flake lock file...")
        self.assertEqual(workspace.lock_file_path.read_text(), "updated lock\n")

    def test_no_update_copies_existing_lock_file(self):
        workflow, runner, workspace = self.make_workflow("--no-update-lock-file")

        flake_dir = workflow.validate_flake()
        workflow.prepare(flake_dir)

        self.assertEqual(workspace.lock_file_path.read_text(), "source lock\n")
        self.assertFalse(any("update" in call["command"] for call in runner.calls))
        build = next(call for call in runner.calls if "build" in call["command"])
        self.assertEqual(
            build["command"][build["command"].index("--reference-lock-file") + 1],
            str(workspace.lock_file_path),
        )

    def test_no_update_without_source_lock_omits_reference_argument(self):
        (self.flake_dir / "flake.lock").unlink()
        workflow, runner, _ = self.make_workflow("--no-update-lock-file")

        flake_dir = workflow.validate_flake()
        workflow.prepare(flake_dir)

        build = next(call for call in runner.calls if "build" in call["command"])
        self.assertNotIn("--reference-lock-file", build["command"])

    def test_failed_update_stops_before_build(self):
        workflow, runner, _ = self.make_workflow(
            runner_options={"update_returncode": 23}
        )

        with self.assertRaises(nix_workflow.NixWorkflowError) as raised:
            workflow.prepare(workflow.validate_flake())

        self.assertEqual(raised.exception.message, "updating lock file error")
        self.assertEqual(raised.exception.exit_code, 23)
        self.assertEqual(len(runner.calls), 1)

    def test_successful_update_without_output_lock_is_error(self):
        workflow, runner, _ = self.make_workflow(
            runner_options={"write_lock_file": False}
        )

        with self.assertRaises(nix_workflow.NixWorkflowError) as raised:
            workflow.prepare(workflow.validate_flake())

        self.assertEqual(raised.exception.message, "updating lock file error")
        self.assertEqual(raised.exception.exit_code, 1)
        self.assertEqual(len(runner.calls), 1)

    def test_failed_build_stops_before_nvd(self):
        workflow, runner, _ = self.make_workflow(
            runner_options={"build_returncode": 31}
        )

        with self.assertRaises(nix_workflow.NixWorkflowError) as raised:
            workflow.prepare(workflow.validate_flake())

        self.assertEqual(
            raised.exception.message, "building nixos system subprocess error"
        )
        self.assertEqual(raised.exception.exit_code, 31)
        self.assertFalse(any(call["command"][0] == "nvd" for call in runner.calls))

    def test_failed_nvd_diff_raises_workflow_error_without_duplicate_message(self):
        workflow, runner, _ = self.make_workflow(runner_options={"nvd_returncode": 12})

        with self.assertRaises(nix_workflow.NixWorkflowError) as raised:
            workflow.prepare(workflow.validate_flake())

        self.assertIsNone(raised.exception.message)
        self.assertEqual(raised.exception.exit_code, 12)
        self.assertEqual(runner.calls[-1]["command"][0], "nvd")

    def test_identical_closures_return_no_changes_without_nvd(self):
        workflow, runner, _ = self.make_workflow(
            "--no-update-lock-file",
            runner_options={"build_target": self.current_closure},
        )

        outcome = workflow.prepare(workflow.validate_flake())

        self.assertEqual(outcome, nix_workflow.NoChanges(str(self.current_closure)))
        self.assertFalse(any(call["command"][0] == "nvd" for call in runner.calls))

    def test_candidate_contains_raw_diff_and_change_counts(self):
        raw_diff = "header one\nheader two\n\033[32m[U.] package 1 -> 2\033[0m\n"
        workflow, _, _ = self.make_workflow(runner_options={"diff_output": raw_diff})

        outcome = workflow.prepare(workflow.validate_flake())

        self.assertIsInstance(outcome, nix_workflow.UpgradeCandidate)
        assert isinstance(outcome, nix_workflow.UpgradeCandidate)
        self.assertEqual(outcome.current_system_closure, str(self.current_closure))
        self.assertEqual(outcome.upgraded_system_closure, str(self.upgraded_closure))
        self.assertEqual(outcome.diff, raw_diff)
        self.assertEqual(outcome.changes, nvd.ChangeCounts(upgraded=1))

    def test_flake_path_with_shell_metacharacters_is_one_argument(self):
        special_flake = self.root / "flake ; $(echo literal)"
        special_flake.mkdir()
        (special_flake / "flake.nix").write_text("{ }")
        (special_flake / "flake.lock").write_text("source lock\n")
        workflow, runner, _ = self.make_workflow(flake_dir=special_flake)

        workflow.prepare(workflow.validate_flake())

        build = next(call for call in runner.calls if "build" in call["command"])
        self.assertEqual(
            build["command"][-1],
            f"{special_flake.resolve()}#nixosConfigurations.test-host.config.system.build.toplevel",
        )


if __name__ == "__main__":
    unittest.main()
