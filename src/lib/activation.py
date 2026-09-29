import base64
import json
from dataclasses import dataclass
from typing import Literal, get_args


HELPER_PATH = "@helper@"
DEFAULT_SUDO_PATH = "/run/wrappers/bin/sudo"

SystemStatus = Literal[
    "switched",
    "invalid-request",
    "stale",
    "profile-failed",
    "switch-failed",
]
LockStatus = Literal["published", "not-requested", "failed", "not-run"]
CommitStatus = Literal[
    "committed",
    "no-changes",
    "not-git",
    "not-requested",
    "failed",
    "not-run",
]


@dataclass(frozen=True)
class ActivationRequest:
    env_path: str
    supported_signals: tuple[int, ...]
    sudo_path: str
    helper_path: str
    expected_current: str
    system_closure: str
    flake_dir: str
    lock_file_bytes: bytes | None
    commit_message: str | None
    no_commit: bool

    def __post_init__(self):
        if self.no_commit and self.commit_message is not None:
            raise ValueError("commit_message must be None when no_commit is true")
        if not self.no_commit and self.commit_message is None:
            raise ValueError("commit_message is required when no_commit is false")


@dataclass(frozen=True)
class ActivationResult:
    system: SystemStatus
    lock: LockStatus
    commit: CommitStatus


def build_activation_command(request: ActivationRequest) -> list[str]:
    ignored_signals = ",".join(str(signum) for signum in request.supported_signals)
    command = [
        request.env_path,
        f"--ignore-signal={ignored_signals}",
        request.sudo_path,
        "--",
        request.helper_path,
        "activate",
        "--expected-current",
        request.expected_current,
        "--system-closure",
        request.system_closure,
        "--flake-dir",
        request.flake_dir,
    ]

    if request.no_commit:
        command.append("--no-commit")

    return command


def encode_activation_request(request: ActivationRequest) -> str:
    lock_file_base64 = (
        base64.b64encode(request.lock_file_bytes).decode("ascii")
        if request.lock_file_bytes is not None
        else None
    )
    commit_message_base64 = (
        base64.b64encode(request.commit_message.encode("utf-8")).decode("ascii")
        if request.commit_message is not None
        else None
    )

    return json.dumps({
        "lock_file_base64": lock_file_base64,
        "commit_message_base64": commit_message_base64,
    }, separators=(",", ":"))


def parse_activation_result(stdout: str, returncode: int) -> ActivationResult:
    try:
        result = json.loads(stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError("invalid activation result JSON") from error

    if not isinstance(result, dict) or set(result) != {"system", "lock", "commit"}:
        raise ValueError("activation result must contain exactly system, lock, and commit")

    system = result["system"]
    lock = result["lock"]
    commit = result["commit"]
    if not all(isinstance(status, str) for status in (system, lock, commit)):
        raise ValueError("activation result statuses must be strings")

    system_statuses = get_args(SystemStatus)
    lock_statuses = get_args(LockStatus)
    commit_statuses = get_args(CommitStatus)

    if system not in system_statuses:
        raise ValueError(f"unknown activation system status: {system!r}")
    if lock not in lock_statuses:
        raise ValueError(f"unknown activation lock status: {lock!r}")
    if commit not in commit_statuses:
        raise ValueError(f"unknown activation commit status: {commit!r}")
    if type(returncode) is not int:
        raise ValueError("activation return code must be an integer")
    if (system == "switched") != (returncode == 0):
        raise ValueError("activation result system status disagrees with exit status")

    return ActivationResult(system=system, lock=lock, commit=commit)
