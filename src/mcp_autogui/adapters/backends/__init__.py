"""Registration of bundled desktop-specific implementations."""

from ...desktop_backend import register_desktop_backend
from .treeland_deepin import BACKEND_ID, create_backend


def register_builtin_backends() -> None:
    register_desktop_backend(BACKEND_ID, create_backend)
