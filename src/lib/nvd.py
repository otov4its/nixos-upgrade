import re
from dataclasses import dataclass


_ANSI_COLOR_RE = re.compile(r"\033\[[0-9;]+m")


@dataclass(frozen=True)
class ChangeCounts:
    added: int = 0
    removed: int = 0
    upgraded: int = 0
    downgraded: int = 0
    changed: int = 0

    @property
    def total(self) -> int:
        return (
            self.added
            + self.removed
            + self.upgraded
            + self.downgraded
            + self.changed
        )


def count_changes(diff: str) -> ChangeCounts:
    plain_diff = _ANSI_COLOR_RE.sub("", diff)
    return ChangeCounts(
        added=len(re.findall(r"\[A.\]", plain_diff)),
        removed=len(re.findall(r"\[R.\]", plain_diff)),
        upgraded=len(re.findall(r"\[U.\]", plain_diff)),
        downgraded=len(re.findall(r"\[D.\]", plain_diff)),
        changed=len(re.findall(r"\[C.\]", plain_diff)),
    )


def format_change_summary(changes: ChangeCounts) -> str:
    if changes.total == 0:
        return "Config changes found"

    details = []
    for name in ("added", "removed", "upgraded", "downgraded", "changed"):
        count = getattr(changes, name)
        if count > 0:
            details.append(f"{count} {name}")

    return f"{changes.total} package changes: {', '.join(details)}"


def format_diff(output: str) -> str:
    return "\n".join(output.split("\n")[2:])
