"""Use grim as the deterministic pyscreenshot backend on Wayland."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence

from PIL import Image


def grab_with_grim(
    bbox: Sequence[int] | None = None,
    childprocess: bool | None = None,
    backend: str | None = None,
) -> Image.Image:
    """Capture the Wayland desktop with grim and optionally crop to bbox."""
    del childprocess, backend
    try:
        result = subprocess.run(
            ["grim", "-"],
            check=True,
            capture_output=True,
            timeout=15,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("grim is required for screenshots on Wayland") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        message = "grim failed to capture the Wayland desktop"
        if detail:
            message = f"{message}: {detail}"
        raise RuntimeError(message) from exc

    with Image.open(io.BytesIO(result.stdout)) as captured:
        image = captured.convert("RGB")
    if bbox is not None:
        image = image.crop(tuple(int(value) for value in bbox))
    return image


def install_grim_backend() -> bool:
    """Make pyscreenshot use grim before pyautogui initializes on Wayland."""
    is_wayland = bool(os.environ.get("WAYLAND_DISPLAY")) or (
        os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    )
    if not sys.platform.startswith("linux") or not is_wayland:
        return False
    if shutil.which("grim") is None:
        raise RuntimeError("grim is required for screenshots on Wayland")

    import pyscreenshot

    pyscreenshot.grab = grab_with_grim
    return True
