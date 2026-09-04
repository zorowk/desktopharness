"""Human-oriented reader for the optional CSV/JSON AutoUI audit archive."""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import sys
import textwrap
from collections import Counter
from pathlib import Path
from typing import Any, Sequence


def _archive_dir(value: str | None) -> Path:
    directory = value or os.environ.get("GUI_AUDIT_DIR")
    if not directory:
        raise ValueError("set GUI_AUDIT_DIR or pass --audit-dir")
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"audit directory does not exist: {root}")
    return root


def _events(root: Path) -> list[dict[str, str]]:
    ledger = root / "ledger.csv"
    if not ledger.is_file():
        return []
    with ledger.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


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
    if isinstance(value, dict) and set(value) == {"__audit_bytes__"}:
        print(json.dumps({"type": "binary-artifact", "base64_bytes": len(value["__audit_bytes__"])}))
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
    print(f"object_bytes: {sum(path.stat().st_size for path in objects)}")
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
    if not (isinstance(value, dict) and set(value) == {"__audit_bytes__"}):
        raise ValueError("object is not a binary artifact")
    target = Path(output).expanduser()
    if target.exists():
        raise ValueError(f"refusing to overwrite existing output: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(base64.b64decode(value["__audit_bytes__"]))
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

    grouped: dict[str, list[dict[str, str]]] = {}
    for event in _events(root):
        grouped.setdefault(event["task_id"], []).append(event)
    curses.wrapper(_run_tui, root, grouped)


def _run_tui(screen, root: Path, grouped: dict[str, list[dict[str, str]]]) -> None:
    screen.keypad(True)
    state, selected, task_id, object_ref = "tasks", 0, None, None
    while True:
        height, width = screen.getmaxyx()
        screen.erase()
        if state == "tasks":
            entries = sorted(grouped)
            event_count = sum(len(events) for events in grouped.values())
            object_dir = root / "objects"
            object_bytes = sum(path.stat().st_size for path in object_dir.glob("*.json")) if object_dir.is_dir() else 0
            _title(
                screen,
                width,
                f"AutoUI v2 audit — {len(entries)} tasks, {event_count} events, {object_bytes} bytes",
                "↑↓ select  Enter timeline  q quit",
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
                is_binary = isinstance(value, dict) and set(value) == {"__audit_bytes__"}
                rendered = (
                    json.dumps({"type": "binary-artifact", "base64_bytes": len(value["__audit_bytes__"])}, indent=2)
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
    screen.addnstr(0, 0, title, width - 1)
    screen.addnstr(1, 0, help_text, width - 1)


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


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(_normalize_argv(argv))
    try:
        root = _archive_dir(args.audit_dir)
        interactive(root)
    except ValueError as exc:
        parser().error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
