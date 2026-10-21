from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"
HEADING = re.compile(r"^## (.+?)\s*$", re.M)
UNRELEASED = "Unreleased"


def sections(text: str) -> list[tuple[str, int, int, int]]:
    found = list(HEADING.finditer(text))
    return [
        (m.group(1).split(" - ")[0], m.start(), m.end(), found[n + 1].start() if n + 1 < len(found) else len(text))
        for n, m in enumerate(found)
    ]


def body(text: str, name: str) -> str | None:
    for title, _, head_end, end in sections(text):
        if title == name:
            return text[head_end:end].strip()
    return None


def notes(text: str, version: str) -> str:
    found = body(text, version)
    if not found:
        raise SystemExit(f"CHANGELOG.md has no entry for {version}.")
    return f"## What's new in {version}\n\n{found}\n"


def release(text: str, version: str, date: str) -> str:
    if body(text, version) is not None:
        raise SystemExit(f"CHANGELOG.md already has an entry for {version}.")
    for title, start, head_end, end in sections(text):
        if title == UNRELEASED:
            changes = text[head_end:end].strip()
            if not changes:
                raise SystemExit("The Unreleased section of CHANGELOG.md is empty. Describe the changes first.")
            entry = f"## {UNRELEASED}\n\n## {version} - {date}\n\n{changes}\n\n"
            return text[:start] + entry + text[end:].lstrip("\n")
    raise SystemExit("CHANGELOG.md has no Unreleased section.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Read or update CHANGELOG.md.")
    commands = parser.add_subparsers(dest="command", required=True)
    show = commands.add_parser("notes", help="print the release notes of a version")
    show.add_argument("version")
    move = commands.add_parser("release", help="turn the Unreleased section into a version")
    move.add_argument("version")
    move.add_argument("date")
    args = parser.parse_args()
    text = CHANGELOG.read_text(encoding="utf-8")
    if args.command == "notes":
        sys.stdout.write(notes(text, args.version))
    else:
        CHANGELOG.write_text(release(text, args.version, args.date), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
