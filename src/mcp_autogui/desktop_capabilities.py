"""Controller-owned Deepin desktop capabilities.

The keybinding schema is useful *evidence* about platform affordances, but its
``triggerValue`` fields must never be exposed as an arbitrary command API.
This module turns the schema into a small, typed capability catalogue and
keeps the executable subset deliberately narrow.
"""

from __future__ import annotations

import json
import re
from configparser import ConfigParser
from pathlib import Path


DEFAULT_KEYBINDING_ROOT = Path("/usr/share/dsg/configs/org.deepin.dde.keybinding")
APP_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Only these platform operations are safe to invoke without treating a schema
# command as executable input.  Applications themselves use dde-am instead.
AUTO_INVOKABLE_CAPABILITIES = frozenset(
    {
        "desktop.launcher.toggle",
        "desktop.search.open",
        "desktop.desktop.show",
        "desktop.window.switch_next",
        "desktop.window.switch_previous",
        "desktop.workspace.next",
        "desktop.workspace.previous",
        "desktop.workspace.1",
        "desktop.workspace.2",
        "desktop.workspace.3",
        "desktop.workspace.4",
        "desktop.workspace.5",
        "desktop.workspace.6",
    }
)

_KNOWN_CAPABILITIES = {
    "app.launcher": "desktop.launcher.toggle",
    "app.globalsearch": "desktop.search.open",
    "app.show-desktop": "desktop.desktop.show",
    "app.taskswitch-next": "desktop.window.switch_next",
    "app.taskswitch-prev": "desktop.window.switch_previous",
    "app.next-workspace": "desktop.workspace.next",
    "app.prev-workspace": "desktop.workspace.previous",
    **{
        f"app.workspace-{number}": f"desktop.workspace.{number}"
        for number in range(1, 7)
    },
}

_RISKY_TOKENS = frozenset(
    {
        "close-window",
        "lockscreen",
        "logout",
        "power",
        "turnoffscreen",
        "eject",
        "screen-recorder",
    }
)

_KEY_NAMES = {
    "Meta": "winleft",
    "Ctrl": "ctrl",
    "Alt": "alt",
    "Shift": "shift",
    "Super": "winleft",
    "Print": "printscreen",
    "Space": "space",
    "Left": "left",
    "Right": "right",
    "Up": "up",
    "Down": "down",
    "Tab": "tab",
    "Escape": "esc",
    "Return": "enter",
}


def _shortcut_slug(path: Path) -> str:
    parent = path.parent.name
    prefix = "org.deepin.dde.keybinding.shortcut."
    return parent[len(prefix):] if parent.startswith(prefix) else parent


def _capability_id(slug: str) -> str:
    return _KNOWN_CAPABILITIES.get(slug, f"deepin.shortcut.{slug}")


def _policy(slug: str, capability_id: str) -> tuple[str, str]:
    if capability_id in AUTO_INVOKABLE_CAPABILITIES:
        return "allow", "low"
    if any(token in slug for token in _RISKY_TOKENS):
        return "confirm", "high"
    return "deny", "medium"


def _normalize_hotkey(hotkey: str) -> list[str]:
    return [_KEY_NAMES.get(part, part.lower()) for part in hotkey.split("+")]


def load_keybinding_catalogue(root: Path = DEFAULT_KEYBINDING_ROOT) -> list[dict]:
    """Load the packaged Deepin default keybinding schema as typed metadata."""
    capabilities = []
    for path in sorted(root.glob("**/org.deepin.shortcut.json")):
        try:
            contents = json.loads(path.read_text(encoding="utf-8")).get("contents", {})
            value = lambda name, default=None: contents.get(name, {}).get("value", default)
            slug = _shortcut_slug(path)
            capability_id = _capability_id(slug)
            policy, risk = _policy(slug, capability_id)
            capabilities.append(
                {
                    "capability_id": capability_id,
                    "schema_id": slug,
                    "label": value("displayName", slug),
                    "hotkeys": value("hotkeys", []),
                    "normalized_hotkeys": [
                        _normalize_hotkey(hotkey) for hotkey in value("hotkeys", [])
                    ],
                    "category": value("category", "Unknown"),
                    "enabled": bool(value("enabled", False)),
                    "trigger_type": value("triggerType"),
                    "source": "default-schema",
                    "policy": policy,
                    "risk": risk,
                    "auto_invokable": (
                        capability_id in AUTO_INVOKABLE_CAPABILITIES
                        and bool(value("enabled", False))
                    ),
                }
            )
        except (OSError, ValueError, json.JSONDecodeError):
            # A malformed third-party schema must not disable all platform facts.
            continue
    return capabilities


def find_capability(capability_id: str, root: Path = DEFAULT_KEYBINDING_ROOT) -> dict | None:
    return next(
        (item for item in load_keybinding_catalogue(root) if item["capability_id"] == capability_id),
        None,
    )


def validate_application_id(app_id: str) -> str:
    if not isinstance(app_id, str):
        raise ValueError("app_id must be a string")
    normalized = app_id.strip()
    if not APP_ID_PATTERN.fullmatch(normalized):
        raise ValueError(
            "app_id must be a Deepin application ID; paths, URIs, options, and commands are not allowed"
        )
    return normalized


def load_desktop_application_catalogue(
    roots: tuple[Path, ...] = (Path("/usr/share/applications"),),
) -> list[dict]:
    """Return discoverable desktop-entry IDs for controller-side resolution.

    This is only a discovery catalogue.  It intentionally does not return the
    desktop-entry ``Exec`` value, because that would turn a data source into an
    arbitrary-command channel.
    """
    applications = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.desktop")):
            parser = ConfigParser(interpolation=None)
            try:
                parser.read(path, encoding="utf-8")
                entry = parser["Desktop Entry"]
                if entry.getboolean("Hidden", fallback=False) or entry.getboolean("NoDisplay", fallback=False):
                    continue
                app_id = path.name.removesuffix(".desktop")
                if not APP_ID_PATTERN.fullmatch(app_id):
                    continue
                applications.append(
                    {
                        "app_id": app_id,
                        "display_name": entry.get("Name", app_id),
                        "display_name_zh_cn": entry.get("Name[zh_CN]"),
                        "source": "desktop-entry-catalogue",
                        "launch_method": "dde-am",
                    }
                )
            except (OSError, KeyError, ValueError):
                continue
    return applications
