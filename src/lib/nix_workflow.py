from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import cli_options
import command_runner
import nvd


NIX_EXTRA_EXPERIMENTAL_FEATURES = [
    "--extra-experimental-features",
    "nix-command flakes",
]


@dataclass(frozen=True)
class NixWorkspace:
    lock_file_path: pathlib.Path
    result_link: pathlib.Path


@dataclass(frozen=True)
class NoChanges:
    current_system_closure: str


@dataclass(frozen=True)
class UpgradeCandidate:
    current_system_closure: str
    upgraded_system_closure: str
    diff: str
    changes: nvd.ChangeCounts


class NixWorkflowRunner(Protocol):
    console: Any

    def run(
        self,
        command: Sequence[str],
        *,
        description: str = "",
        output_policy: command_runner.OutputPolicy = command_runner.OutputPolicy.MERGE_STDERR,
    ) -> subprocess.CompletedProcess[str]: ...


class NixWorkflowError(Exception):
    def __init__(self, message: str | None, exit_code: int):
        super().__init__(message or "")
        self.message = message
        self.exit_code = exit_code


class NixWorkflow:
    def __init__(
        self,
        options: cli_options.Options,
        runner: NixWorkflowRunner,
        workspace: NixWorkspace,
        current_system_closure: str,
    ):
        self.options = options
        self.runner = runner
        self.workspace = workspace
        self.current_system_closure = current_system_closure

    def validate_flake(self) -> pathlib.Path:
        flake_dir = self.options.flake
        self.runner.console.logger.debug(f"{flake_dir=}")

        try:
            resolved_flake_dir = flake_dir.resolve(strict=True)
        except OSError:
            raise NixWorkflowError(f"{flake_dir}: no such directory", 1) from None

        self.runner.console.logger.debug(f"  resolved to {resolved_flake_dir!r}")
        if not resolved_flake_dir.is_dir():
            raise NixWorkflowError(f"{resolved_flake_dir}: no such directory", 1)
        if not (resolved_flake_dir / "flake.nix").is_file():
            raise NixWorkflowError(f"{resolved_flake_dir}: this dir is not a flake", 1)

        if self.options.no_update_lock_file:
            source_lock_file = resolved_flake_dir / "flake.lock"
            if source_lock_file.is_file():
                try:
                    shutil.copyfile(source_lock_file, self.workspace.lock_file_path)
                except OSError as error:
                    raise NixWorkflowError(
                        f"preparing temporary lock file failed: {error}", 1
                    ) from error

        return resolved_flake_dir

    def prepare(self, flake_dir: pathlib.Path) -> NoChanges | UpgradeCandidate:
        if not self.options.no_update_lock_file:
            self._update_lock_file(flake_dir)

        upgraded_system_closure = self._build_system(flake_dir)
        if upgraded_system_closure == self.current_system_closure:
            return NoChanges(self.current_system_closure)

        diff_result = self.runner.run(
            [
                "nvd",
                "--color=always",
                "diff",
                self.current_system_closure,
                upgraded_system_closure,
            ],
            description="Comparing derivations...",
        )
        if diff_result.returncode != os.EX_OK:
            raise NixWorkflowError(None, diff_result.returncode)

        diff = diff_result.stdout or ""
        return UpgradeCandidate(
            current_system_closure=self.current_system_closure,
            upgraded_system_closure=upgraded_system_closure,
            diff=diff,
            changes=nvd.count_changes(diff),
        )

    def _update_lock_file(self, flake_dir: pathlib.Path) -> None:
        command = [
            "nix",
            *NIX_EXTRA_EXPERIMENTAL_FEATURES,
            "flake",
            "update",
        ]
        if self.options.inputs:
            command.extend(self.options.inputs)
        command.extend(
            [
                "--flake",
                str(flake_dir),
                "--output-lock-file",
                str(self.workspace.lock_file_path),
            ]
        )
        update = self.runner.run(
            command,
            description="updating flake lock file...",
            output_policy=command_runner.OutputPolicy.INHERIT_STDERR,
        )

        if update.returncode != os.EX_OK or not self.workspace.lock_file_path.is_file():
            raise NixWorkflowError("updating lock file error", update.returncode or 1)

    def _build_system(self, flake_dir: pathlib.Path) -> str:
        nixos_config = (
            f"{flake_dir}#nixosConfigurations."
            f"{self.options.configuration}.config.system.build.toplevel"
        )
        self.runner.console.logger.debug(f"{nixos_config=}")

        command = [
            "nix",
            *NIX_EXTRA_EXPERIMENTAL_FEATURES,
            "build",
            "--out-link",
            str(self.workspace.result_link),
            "--no-write-lock-file",
        ]
        if self.workspace.lock_file_path.is_file():
            command.extend(
                [
                    "--reference-lock-file",
                    str(self.workspace.lock_file_path),
                ]
            )
        command.append(nixos_config)

        build = self.runner.run(
            command,
            description="building nixos system...",
            output_policy=command_runner.OutputPolicy.INHERIT_STDERR,
        )
        if build.returncode != os.EX_OK:
            raise NixWorkflowError(
                "building nixos system subprocess error", build.returncode
            )

        try:
            upgraded_system_closure = str(
                self.workspace.result_link.resolve(strict=True)
            )
        except OSError:
            raise NixWorkflowError(
                "could not resolve built system closure", 1
            ) from None

        self.runner.console.logger.debug(f"{upgraded_system_closure=}")
        return upgraded_system_closure
