"""Finite fact-path registry shared by evidence providers and assertions."""

STANDARD_FACT_PATHS = frozenset(
    {
        "active_window.app_id",
        "active_window.window_id",
        "active_window.title",
        "window.exists",
        "window.geometry",
        "window.visible",
        "cursor.position",
        "clipboard.text",
        "process.running",
        "file.exists",
        "file.content",
        "control.name",
        "control.role",
        "control.value",
        "document.text",
        "application.result",
    }
)


def require_standard_fact_path(path: str) -> None:
    if path not in STANDARD_FACT_PATHS:
        raise ValueError(f"unregistered fact path: {path}")

