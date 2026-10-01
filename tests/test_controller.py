import importlib.util
import io
import os
import pathlib
import signal
import sys
import types
import unittest
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "src" / "lib" / "nixos-upgrade.py"
LIBRARY_DIR = MODULE_PATH.parent
sys.path.insert(0, str(LIBRARY_DIR))

termcolor = types.ModuleType("termcolor")
setattr(termcolor, "colored", lambda text, *args, **kwargs: text)
yaspin = types.ModuleType("yaspin")
yaspin.__path__ = []
setattr(yaspin, "yaspin", lambda *args, **kwargs: None)
spinners = types.ModuleType("yaspin.spinners")
setattr(spinners, "Spinners", types.SimpleNamespace(point=object()))
setattr(yaspin, "spinners", spinners)

with mock.patch.dict(
    sys.modules,
    {
        "termcolor": termcolor,
        "yaspin": yaspin,
        "yaspin.spinners": spinners,
    },
):
    spec = importlib.util.spec_from_file_location(
        "nixos_upgrade_controller_test", MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module from {MODULE_PATH}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)


class FakeWorkflow:
    def __init__(self, outcome=None, error=None):
        self.current_system_closure = "/nix/store/current-system"
        self.workspace = types.SimpleNamespace(
            lock_file_path=pathlib.Path("/tmp/nixos-upgrade-test/flake.lock")
        )
        self.validate_flake = mock.Mock(return_value=pathlib.Path("/etc/nixos"))
        self.prepare = mock.Mock(return_value=outcome, side_effect=error)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        defaults = module.cli_options.RuntimeDefaults(
            name="nixos-upgrade-test",
            version="test",
            hostname="test-host",
            default_flake=pathlib.Path("/etc/nixos"),
        )
        self.options = module.cli_options.parse_args([], defaults)
        self.console = mock.Mock()
        self.console.stdout = io.StringIO()
        self.runner = mock.Mock()
        self.candidate = module.nix_workflow.UpgradeCandidate(
            current_system_closure="/nix/store/current-system",
            upgraded_system_closure="/nix/store/upgraded-system",
            diff="header one\nheader two\n[U.] package 1 -> 2\n",
            changes=module.nvd.ChangeCounts(upgraded=1),
        )

    def make_program(self, *, outcome=None, error=None):
        workflow = FakeWorkflow(outcome=outcome, error=error)
        program = module.CliProgram(
            self.options,
            self.console,
            self.runner,
            workflow,
        )
        program.run_privileged_activation = mock.Mock(
            return_value=types.SimpleNamespace(system="switched")
        )
        program.std_streams_to_devnull = mock.Mock()
        return program, workflow

    def test_no_changes_skips_confirmation_and_activation(self):
        program, _ = self.make_program(
            outcome=module.nix_workflow.NoChanges("/nix/store/current-system")
        )

        status = program.run()

        self.assertEqual(status, os.EX_OK)
        self.assertEqual(self.console.stdout.getvalue(), "no changes found\n")
        self.console.confirm.assert_not_called()
        program.run_privileged_activation.assert_not_called()

    def test_decline_does_not_invoke_activation(self):
        program, workflow = self.make_program(outcome=self.candidate)
        self.console.confirm.return_value = False

        status = program.run()

        self.assertEqual(status, os.EX_OK)
        workflow.validate_flake.assert_called_once_with()
        workflow.prepare.assert_called_once_with(pathlib.Path("/etc/nixos"))
        self.console.confirm.assert_called_once()
        self.assertEqual(self.console.stdout.getvalue(), "nothing changed\n")
        program.run_privileged_activation.assert_not_called()

    def test_confirmed_candidate_invokes_activation_once(self):
        program, _ = self.make_program(outcome=self.candidate)
        self.console.confirm.return_value = True

        status = program.run()

        self.assertEqual(status, os.EX_OK)
        program.run_privileged_activation.assert_called_once_with()
        self.assertEqual(self.console.stdout.getvalue(), "system upgraded\n")

    def test_workflow_error_preserves_exit_status_without_activation(self):
        program, _ = self.make_program(
            error=module.nix_workflow.NixWorkflowError("building failed", 37)
        )

        status = program.run()

        self.assertEqual(status, 37)
        self.console.logger.error.assert_called_with("building failed")
        self.console.confirm.assert_not_called()
        program.run_privileged_activation.assert_not_called()

    def test_supported_signal_preserves_signal_exit_status(self):
        program, _ = self.make_program(outcome=self.candidate)
        with mock.patch.object(
            module.synsignals,
            "handle",
            side_effect=lambda: program.termination_signal_handler(
                signal.SIGTERM, None
            ),
        ):
            status = program.run()

        self.assertEqual(status, 128 + signal.SIGTERM)
        self.runner.terminate_active.assert_called_once_with()
        self.console.logger.error.assert_called_once()
        self.console.confirm.assert_not_called()

    def test_broken_pipe_redirects_closed_streams_and_preserves_sigpipe_status(self):
        program, _ = self.make_program(outcome=self.candidate)
        self.console.display_diff.side_effect = BrokenPipeError

        status = program.run()

        self.assertEqual(status, 128 + signal.SIGPIPE)
        program.std_streams_to_devnull.assert_called_once_with()
        self.console.confirm.assert_not_called()


if __name__ == "__main__":
    unittest.main()
