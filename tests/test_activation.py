import base64
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "lib"))

from activation import (
    ActivationRequest,
    build_activation_command,
    encode_activation_request,
    parse_activation_result,
)


class ActivationCommandTests(unittest.TestCase):
    def make_request(self, **overrides):
        values = {
            "env_path": "/nix/store/core utils/bin/env",
            "supported_signals": (1, 2, 15),
            "sudo_path": "/run/wrappers/bin/sudo",
            "helper_path": "/nix/store/helper path/lib/nixos-upgrade-activate",
            "expected_current": "/nix/store/old-closure",
            "system_closure": "/nix/store/new-closure",
            "flake_dir": "/etc/nixos; echo unsafe",
            "lock_file_bytes": b'{"nodes": {}}',
            "commit_message": "Upgrade\nwith details\n",
            "no_commit": False,
        }
        values.update(overrides)
        return ActivationRequest(**values)

    def test_command_keeps_paths_as_arguments_and_ignores_supported_signals(self):
        request = self.make_request()

        command = build_activation_command(request)

        self.assertEqual(command, [
            "/nix/store/core utils/bin/env",
            "--ignore-signal=1,2,15",
            "/run/wrappers/bin/sudo",
            "--",
            "/nix/store/helper path/lib/nixos-upgrade-activate",
            "activate",
            "--expected-current",
            "/nix/store/old-closure",
            "--system-closure",
            "/nix/store/new-closure",
            "--flake-dir",
            "/etc/nixos; echo unsafe",
        ])
        self.assertNotIn("sh", command)
        self.assertNotIn("-c", command)

    def test_command_adds_no_commit_only_when_requested(self):
        commit_command = build_activation_command(self.make_request())
        no_commit_command = build_activation_command(self.make_request(
            commit_message=None,
            no_commit=True,
        ))

        self.assertNotIn("--no-commit", commit_command)
        self.assertEqual(no_commit_command[-1], "--no-commit")

    def test_request_rejects_commit_message_when_commits_are_disabled(self):
        with self.assertRaises(ValueError):
            self.make_request(no_commit=True)

    def test_request_requires_commit_message_when_commits_are_enabled(self):
        with self.assertRaises(ValueError):
            self.make_request(commit_message=None)


class ActivationManifestTests(unittest.TestCase):
    def test_manifest_contains_only_base64_file_contents(self):
        lock_bytes = b'\x00\xfflock\n'
        commit_message = "Upgrade\nwith details\n"
        request = ActivationRequest(
            env_path="/bin/env",
            supported_signals=(2, 15),
            sudo_path="/run/wrappers/bin/sudo",
            helper_path="/nix/store/helper/lib/nixos-upgrade-activate",
            expected_current="/nix/store/current",
            system_closure="/nix/store/new",
            flake_dir="/etc/nixos",
            lock_file_bytes=lock_bytes,
            commit_message=commit_message,
            no_commit=False,
        )

        manifest = json.loads(encode_activation_request(request))

        self.assertEqual(set(manifest), {
            "lock_file_base64",
            "commit_message_base64",
        })
        self.assertEqual(
            base64.b64decode(manifest["lock_file_base64"], validate=True),
            lock_bytes,
        )
        self.assertEqual(
            base64.b64decode(manifest["commit_message_base64"], validate=True),
            commit_message.encode("utf-8"),
        )
        self.assertNotIn("/etc/nixos", json.dumps(manifest))
        self.assertNotIn("/tmp", json.dumps(manifest))

    def test_manifest_uses_null_for_unrequested_data(self):
        request = ActivationRequest(
            env_path="/bin/env",
            supported_signals=(2, 15),
            sudo_path="/run/wrappers/bin/sudo",
            helper_path="/nix/store/helper/lib/nixos-upgrade-activate",
            expected_current="/nix/store/current",
            system_closure="/nix/store/new",
            flake_dir="/etc/nixos",
            lock_file_bytes=None,
            commit_message=None,
            no_commit=True,
        )

        self.assertEqual(json.loads(encode_activation_request(request)), {
            "lock_file_base64": None,
            "commit_message_base64": None,
        })


class ActivationResultTests(unittest.TestCase):
    def test_parser_accepts_every_documented_status_with_consistent_exit_code(self):
        cases = [
            ({"system": "switched", "lock": "published", "commit": "committed"}, 0),
            ({"system": "switched", "lock": "not-requested", "commit": "no-changes"}, 0),
            ({"system": "switched", "lock": "failed", "commit": "failed"}, 0),
            ({"system": "stale", "lock": "not-run", "commit": "not-run"}, 1),
            ({"system": "profile-failed", "lock": "not-run", "commit": "not-run"}, 1),
            ({"system": "switch-failed", "lock": "not-run", "commit": "not-run"}, 1),
            ({"system": "invalid-request", "lock": "not-run", "commit": "not-run"}, 2),
            ({"system": "switched", "lock": "published", "commit": "not-git"}, 0),
            ({"system": "switched", "lock": "not-requested", "commit": "not-requested"}, 0),
        ]

        for result, returncode in cases:
            with self.subTest(result=result):
                stdout = json.dumps(result) + "\n"
                parsed = parse_activation_result(stdout, returncode)
                self.assertEqual(parsed.system, result["system"])
                self.assertEqual(parsed.lock, result["lock"])
                self.assertEqual(parsed.commit, result["commit"])

    def test_parser_rejects_malformed_missing_or_unknown_status(self):
        invalid_results = [
            "not json",
            "{}",
            json.dumps({"system": "switched", "lock": "published"}),
            json.dumps({"system": "unknown", "lock": "published", "commit": "committed"}),
            json.dumps({"system": "switched", "lock": "unknown", "commit": "committed"}),
            json.dumps({"system": "switched", "lock": "published", "commit": "unknown"}),
            json.dumps({"system": ["switched"], "lock": "published", "commit": "committed"}),
            json.dumps({"system": "switched", "lock": {"status": "published"}, "commit": "committed"}),
            json.dumps({"system": "switched", "lock": "published", "commit": "committed", "extra": "field"}),
            "{}\n{}",
        ]

        for stdout in invalid_results:
            with self.subTest(stdout=stdout):
                with self.assertRaises(ValueError):
                    parse_activation_result(stdout, 0)

    def test_parser_rejects_exit_status_inconsistent_with_system_status(self):
        switched = json.dumps({
            "system": "switched",
            "lock": "published",
            "commit": "committed",
        })
        stale = json.dumps({
            "system": "stale",
            "lock": "not-run",
            "commit": "not-run",
        })

        with self.assertRaises(ValueError):
            parse_activation_result(switched, 1)
        with self.assertRaises(ValueError):
            parse_activation_result(stale, 0)


if __name__ == "__main__":
    unittest.main()
