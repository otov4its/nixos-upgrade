import argparse
import enum
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


class ColorOption(enum.StrEnum):
    AUTO = enum.auto()
    ALWAYS = enum.auto()
    NEVER = enum.auto()


@dataclass(frozen=True)
class RuntimeDefaults:
    name: str
    version: str
    hostname: str
    default_flake: Path


@dataclass(frozen=True)
class Options:
    flake: Path
    configuration: str
    no_update_lock_file: bool
    inputs: tuple[str, ...] | None
    commit_message: str
    assume_yes: bool
    assume_no: bool
    no_commit: bool
    verbosity: int
    color: ColorOption


class CliUsageError(Exception):
    def __init__(self, message: str, options: Options):
        super().__init__(message)
        self.options = options


def _make_parser(
    defaults: RuntimeDefaults, *, enforce_exclusive_groups: bool = True
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=defaults.name,
        description="Updates nixos flake and shows changed packages",
        exit_on_error=False,
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=defaults.version,
    )
    parser.add_argument(
        "--flake",
        help=f"Nixos flake dir (default: {defaults.default_flake})",
        default=defaults.default_flake,
        type=Path,
    )
    parser.add_argument(
        "-C",
        "--configuration",
        metavar="NAME",
        help=f"NixOS configuration to build (default: {defaults.hostname})",
        default=defaults.hostname,
    )
    lock_update_group = (
        parser.add_mutually_exclusive_group() if enforce_exclusive_groups else parser
    )
    lock_update_group.add_argument(
        "-u",
        "--no-update-lock-file",
        action="store_true",
        help="do not update flake.lock",
    )
    lock_update_group.add_argument(
        "--inputs",
        metavar="NAME",
        nargs="+",
        help="update only the named flake inputs (default: update all)",
    )
    parser.add_argument(
        "-m", "--commit-message", help="add a commit message", default="", type=str
    )
    assume_group = (
        parser.add_mutually_exclusive_group() if enforce_exclusive_groups else parser
    )
    assume_group.add_argument(
        "-y",
        "--assume-yes",
        action="store_true",
        help=(
            "when a yes/no prompt would be presented, "
            'assume that the user entered "yes". '
            "In particular, suppresses the prompt that "
            "appears when upgrading system."
        ),
    )
    assume_group.add_argument(
        "-n", "--assume-no", action="store_true", help="likewise --assume-yes"
    )
    parser.add_argument(
        "-c", "--no-commit", action="store_true", help="do not commit a flake repo"
    )
    parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="increase verbosity"
    )
    parser.add_argument(
        "-q", "--quiet", action="count", default=0, help="decrease verbosity"
    )
    parser.add_argument(
        "--color",
        choices=[option.value for option in ColorOption],
        default=ColorOption.AUTO,
        help="when to display output using colors",
    )
    return parser


def parse_args(argv: Sequence[str], defaults: RuntimeDefaults) -> Options:
    arguments = list(argv)
    parser = _make_parser(defaults)
    try:
        namespace, unknown = parser.parse_known_args(arguments)
    except argparse.ArgumentError as error:
        permissive_parser = _make_parser(defaults, enforce_exclusive_groups=False)
        try:
            partial_namespace, _ = permissive_parser.parse_known_args(arguments)
        except argparse.ArgumentError:
            partial_namespace, _ = permissive_parser.parse_known_args([])
        options = _options_from_namespace(partial_namespace)
        raise CliUsageError(str(error), options) from error

    options = _options_from_namespace(namespace)
    if unknown:
        raise CliUsageError("unrecognized arguments: " + " ".join(unknown), options)

    return options


def _options_from_namespace(namespace: argparse.Namespace) -> Options:
    inputs = tuple(namespace.inputs) if namespace.inputs is not None else None
    return Options(
        flake=namespace.flake,
        configuration=namespace.configuration,
        no_update_lock_file=namespace.no_update_lock_file,
        inputs=inputs,
        commit_message=namespace.commit_message,
        assume_yes=namespace.assume_yes,
        assume_no=namespace.assume_no,
        no_commit=namespace.no_commit,
        verbosity=namespace.verbose - namespace.quiet,
        color=ColorOption(namespace.color),
    )
