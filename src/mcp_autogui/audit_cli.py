"""Human-oriented reader for the optional CSV/JSON AutoUI audit archive."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tarfile
import tempfile
import textwrap
import time
from contextlib import contextmanager
from collections import Counter
from pathlib import Path
from typing import Any, Sequence


def _archive_path(value: str | None) -> Path:
    directory = value or os.environ.get("GUI_AUDIT_DIR")
    if not directory:
        raise ValueError("set GUI_AUDIT_DIR or pass --audit-dir")
    root = Path(directory).expanduser().resolve()
    if not root.exists():
        raise ValueError(f"audit path does not exist: {root}")
    return root


@contextmanager
def _open_archive(path: Path):
    if path.is_dir():
        yield path
        return
    if not path.name.endswith(".tar.gz"):
        raise ValueError("audit archive must be a directory or .tar.gz file")
    with tempfile.TemporaryDirectory(prefix="autoui-audit-") as temporary:
        root = Path(temporary)
        with tarfile.open(path, "r:gz") as archive:
            for member in archive.getmembers():
                member_path = Path(member.name)
                allowed = member.name == "ledger.csv" or (
                    len(member_path.parts) == 2
                    and (
                        (member_path.parts[0] == "objects" and member_path.suffix == ".json")
                        or (member_path.parts[0] == "artifacts" and member_path.suffix == ".bin")
                    )
                )
                if not allowed or not member.isfile() or member_path.is_absolute() or ".." in member_path.parts:
                    raise ValueError(f"unsafe audit archive member: {member.name}")
                destination = root / member_path
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"cannot read audit archive member: {member.name}")
                destination.write_bytes(source.read())
        yield root


def create_archive(root: Path, output: str) -> Path:
    target = Path(output).expanduser()
    if target.suffixes[-2:] != [".tar", ".gz"]:
        target = target.with_name(f"{target.name}.tar.gz")
    if target.exists():
        raise ValueError(f"refusing to overwrite existing output: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(target, "w:gz") as archive:
        ledger = root / "ledger.csv"
        if ledger.is_file():
            archive.add(ledger, arcname="ledger.csv", recursive=False)
        objects = root / "objects"
        if objects.is_dir():
            for object_file in sorted(objects.glob("*.json")):
                archive.add(object_file, arcname=f"objects/{object_file.name}", recursive=False)
        artifacts = root / "artifacts"
        if artifacts.is_dir():
            for artifact_file in sorted(artifacts.glob("*.bin")):
                archive.add(artifact_file, arcname=f"artifacts/{artifact_file.name}", recursive=False)
    return target


def _events(root: Path) -> list[dict[str, str]]:
    ledger = root / "ledger.csv"
    if not ledger.is_file():
        return []
    with ledger.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _archive_bytes(root: Path) -> int:
    files = [root / "ledger.csv"]
    for directory, pattern in ((root / "objects", "*.json"), (root / "artifacts", "*.bin")):
        if directory.is_dir():
            files.extend(directory.glob(pattern))
    return sum(path.stat().st_size for path in files if path.is_file())


def _object_path(root: Path, reference: str) -> Path:
    if not reference or "/" in reference or "\\" in reference or reference.startswith("."):
        raise ValueError("invalid object reference")
    return root / "objects" / f"{reference}.json"


def _load_object(root: Path, reference: str) -> Any:
    path = _object_path(root, reference)
    if not path.is_file():
        raise ValueError(f"archived object not found: {reference}")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle).get("value")


def _print_json(value: Any) -> None:
    if isinstance(value, dict) and set(value) == {"__audit_artifact__"}:
        print(json.dumps({"type": "binary-artifact", **value["__audit_artifact__"]}))
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def summary(root: Path) -> None:
    events = _events(root)
    objects = list((root / "objects").glob("*.json")) if (root / "objects").is_dir() else []
    task_ids = {event["task_id"] for event in events}
    print(f"archive: {root}")
    print(f"tasks: {len(task_ids)}")
    print(f"events: {len(events)}")
    print(f"objects: {len(objects)}")
    print(f"archive_bytes: {_archive_bytes(root)}")
    for event_type, count in Counter(event["event_type"] for event in events).most_common():
        print(f"{event_type}: {count}")


def tasks(root: Path) -> None:
    grouped: dict[str, list[dict[str, str]]] = {}
    for event in _events(root):
        grouped.setdefault(event["task_id"], []).append(event)
    for task_id, events in sorted(grouped.items()):
        print(f"{task_id}\tevents={len(events)}\tfirst={events[0]['occurred_at']}\tlast={events[-1]['occurred_at']}")


def timeline(root: Path, task_id: str) -> None:
    events = [event for event in _events(root) if event["task_id"] == task_id]
    if not events:
        raise ValueError(f"no archived events for task: {task_id}")
    for event in events:
        reference = event["object_ref"]
        archived = "archived" if _object_path(root, reference).is_file() else "missing"
        print(
            f"{event['sequence']:>3} {event['occurred_at']} {event['event_type']}\n"
            f"    object={reference} ({archived})  snapshot={event['snapshot_id'] or '-'}\n"
            f"    caused_by={event['caused_by']}  artifacts={event['artifact_refs']}"
        )


def extract(root: Path, reference: str, output: str) -> None:
    value = _load_object(root, reference)
    if not (isinstance(value, dict) and set(value) == {"__audit_artifact__"}):
        raise ValueError("object is not a binary artifact")
    target = Path(output).expanduser()
    if target.exists():
        raise ValueError(f"refusing to overwrite existing output: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    metadata = value["__audit_artifact__"]
    artifact = root / str(metadata.get("path", ""))
    if artifact.parent != root / "artifacts" or not artifact.is_file():
        raise ValueError("archived artifact file is missing")
    payload = artifact.read_bytes()
    if __import__("hashlib").sha256(payload).hexdigest() != metadata.get("sha256"):
        raise ValueError("archived artifact checksum mismatch")
    target.write_bytes(payload)
    print(target)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Inspect a compositor-neutral AutoUI v2 audit archive.")
    result.add_argument("--audit-dir", help="archive directory (defaults to GUI_AUDIT_DIR)")
    return result


def _normalize_argv(argv: Sequence[str] | None) -> list[str]:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) == 1 and not arguments[0].startswith("-"):
        return ["--audit-dir", arguments[0]]
    return arguments


def interactive(root: Path) -> None:
    """Browse task timelines and stored objects without compositor-specific code."""
    import curses

    curses.wrapper(_run_tui, root)


def _run_tui(screen, root: Path) -> None:
    _boot_sequence(screen, root)
    grouped: dict[str, list[dict[str, str]]] = {}
    for event in _events(root):
        grouped.setdefault(event["task_id"], []).append(event)
    screen.keypad(True)
    state, selected, task_id, object_ref = "tasks", 0, None, None
    while True:
        height, width = screen.getmaxyx()
        screen.erase()
        if state == "tasks":
            entries = sorted(grouped)
            event_count = sum(len(events) for events in grouped.values())
            archive_bytes = _archive_bytes(root)
            _title(
                screen,
                width,
                f"[ AUTOUI // AUDIT ]  {len(entries)} TASKS :: {event_count} EVENTS :: {archive_bytes} BYTES",
                "↑↓ select  Enter timeline  z compress  q quit",
            )
            if not entries:
                screen.addnstr(2, 0, "No events in ledger.csv", width - 1)
            for index, current_task in enumerate(entries[: max(1, height - 3)]):
                marker = "> " if index == selected else "  "
                screen.addnstr(2 + index, 0, f"{marker}{current_task}  ({len(grouped[current_task])} events)", width - 1)
            key = screen.getch()
            if key in (ord("q"), 27):
                return
            if entries and key == curses.KEY_DOWN:
                selected = min(selected + 1, len(entries) - 1)
            elif entries and key == curses.KEY_UP:
                selected = max(selected - 1, 0)
            elif entries and key in (10, 13, curses.KEY_ENTER):
                task_id, state, selected = entries[selected], "timeline", 0
            elif key == ord("z"):
                _archive_prompt(screen, root)
        elif state == "timeline":
            entries = grouped[task_id]
            _title(screen, width, f"Task {task_id}", "↑↓ select  Enter object  b back  q quit")
            for index, event in enumerate(entries[: max(1, height - 3)]):
                marker = "> " if index == selected else "  "
                line = f"{marker}{event['sequence']:>3}  {event['event_type']}  {event['object_ref']}"
                screen.addnstr(2 + index, 0, line, width - 1)
            key = screen.getch()
            if key in (ord("q"), 27):
                return
            if key == ord("b"):
                state, selected = "tasks", 0
            elif key == curses.KEY_DOWN:
                selected = min(selected + 1, len(entries) - 1)
            elif key == curses.KEY_UP:
                selected = max(selected - 1, 0)
            elif key in (10, 13, curses.KEY_ENTER):
                object_ref, state = entries[selected]["object_ref"], "object"
        else:
            is_binary = False
            try:
                value = _load_object(root, object_ref)
                is_binary = isinstance(value, dict) and set(value) == {"__audit_artifact__"}
                rendered = (
                    json.dumps({"type": "binary-artifact", **value["__audit_artifact__"]}, indent=2)
                    if is_binary
                    else json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
                )
            except ValueError as exc:
                rendered = str(exc)
            _title(
                screen,
                width,
                f"Object {object_ref}",
                "e export binary  b back  q quit" if is_binary else "b back  q quit",
            )
            row = 2
            for line in rendered.splitlines():
                for fragment in textwrap.wrap(line, width=max(10, width - 1), replace_whitespace=False) or [""]:
                    if row >= height:
                        break
                    screen.addnstr(row, 0, fragment, width - 1)
                    row += 1
            key = screen.getch()
            if key in (ord("q"), 27):
                return
            if key == ord("b"):
                state = "timeline"
            elif is_binary and key == ord("e"):
                _export_prompt(screen, root, object_ref)


def _title(screen, width: int, title: str, help_text: str) -> None:
    import curses

    screen.attron(curses.A_BOLD | curses.color_pair(1))
    screen.addnstr(0, 0, title, width - 1)
    screen.attroff(curses.A_BOLD | curses.color_pair(1))
    screen.attron(curses.color_pair(2))
    screen.addnstr(1, 0, f":: {help_text}", width - 1)
    screen.attroff(curses.color_pair(2))


def _boot_sequence(screen, root: Path) -> None:
    """Short read-only archive scan to make startup state visible."""
    import curses

    try:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)
        curses.init_pair(2, curses.COLOR_CYAN, -1)
    except curses.error:
        pass
    screen.nodelay(True)
    steps = (
        "mounting read-only audit archive",
        "scanning ledger.csv",
        "indexing object references",
        "reconstructing causal links",
        "console online",
    )
    spinner = "|/-\\"
    for index, step in enumerate(steps):
        height, width = screen.getmaxyx()
        screen.erase()
        screen.attron(curses.A_BOLD | curses.color_pair(1))
        screen.addnstr(1, 2, "AUTOUI // FORENSIC AUDIT CONSOLE", width - 4)
        screen.attroff(curses.A_BOLD | curses.color_pair(1))
        screen.attron(curses.color_pair(2))
        screen.addnstr(3, 2, f"TARGET  {root}", width - 4)
        screen.addnstr(5, 2, f"[{spinner[index % len(spinner)]}] {step.upper()}", width - 4)
        for completed in steps[:index]:
            screen.addnstr(7 + list(steps).index(completed), 4, f"[OK] {completed}", width - 6)
        screen.addnstr(height - 2, 2, "press any key to skip animation", width - 4)
        screen.attroff(curses.color_pair(2))
        screen.refresh()
        if screen.getch() != -1:
            break
        time.sleep(0.11)
    screen.nodelay(False)


def _export_prompt(screen, root: Path, reference: str) -> None:
    import curses

    height, width = screen.getmaxyx()
    prompt = "Export path (blank cancels): "
    screen.move(height - 1, 0)
    screen.clrtoeol()
    screen.addnstr(height - 1, 0, prompt, width - 1)
    curses.echo()
    try:
        raw = screen.getstr(height - 1, min(len(prompt), width - 1), max(1, width - len(prompt) - 1))
    finally:
        curses.noecho()
    output = raw.decode("utf-8").strip()
    if output:
        try:
            extract(root, reference, output)
            message = f"Exported: {output}"
        except ValueError as exc:
            message = str(exc)
        screen.move(height - 1, 0)
        screen.clrtoeol()
        screen.addnstr(height - 1, 0, message, width - 1)
        screen.getch()


def _archive_prompt(screen, root: Path) -> None:
    import curses

    height, width = screen.getmaxyx()
    prompt = "Compressed archive path (blank cancels): "
    screen.move(height - 1, 0)
    screen.clrtoeol()
    screen.addnstr(height - 1, 0, prompt, width - 1)
    curses.echo()
    try:
        raw = screen.getstr(height - 1, min(len(prompt), width - 1), max(1, width - len(prompt) - 1))
    finally:
        curses.noecho()
    output = raw.decode("utf-8").strip()
    if output:
        try:
            archive = create_archive(root, output)
            message = f"Compressed archive: {archive}"
        except ValueError as exc:
            message = str(exc)
        screen.move(height - 1, 0)
        screen.clrtoeol()
        screen.addnstr(height - 1, 0, message, width - 1)
        screen.getch()


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(_normalize_argv(argv))
    try:
        with _open_archive(_archive_path(args.audit_dir)) as root:
            interactive(root)
    except ValueError as exc:
        parser().error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
