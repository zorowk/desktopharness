"""Image preparation used by the embedded Qwen-CUA agent."""

from __future__ import annotations

import base64
import math
from io import BytesIO

from PIL import Image


def _round_by_factor(number: float, factor: int) -> int:
    return round(number / factor) * factor


def _ceil_by_factor(number: float, factor: int) -> int:
    return math.ceil(number / factor) * factor


def _floor_by_factor(number: float, factor: int) -> int:
    return math.floor(number / factor) * factor


def smart_resize(
    height: int,
    width: int,
    *,
    factor: int = 32,
    min_pixels: int = 56 * 56,
    max_pixels: int = 16 * 16 * 4 * 12800,
    max_long_side: int = 8192,
) -> tuple[int, int]:
    if height < 2 or width < 2:
        raise ValueError("Screenshot dimensions must both be at least 2 pixels")
    if max(height, width) / min(height, width) > 200:
        raise ValueError("Screenshot aspect ratio must not exceed 200")
    if factor < 1:
        raise ValueError("resize factor must be positive")

    resized_height, resized_width = height, width
    if max(resized_height, resized_width) > max_long_side:
        scale = max(resized_height, resized_width) / max_long_side
        resized_height = int(resized_height / scale)
        resized_width = int(resized_width / scale)

    target_height = max(factor, _round_by_factor(resized_height, factor))
    target_width = max(factor, _round_by_factor(resized_width, factor))
    if target_height * target_width > max_pixels:
        scale = math.sqrt((resized_height * resized_width) / max_pixels)
        target_height = max(factor, _floor_by_factor(resized_height / scale, factor))
        target_width = max(factor, _floor_by_factor(resized_width / scale, factor))
    elif target_height * target_width < min_pixels:
        scale = math.sqrt(min_pixels / (resized_height * resized_width))
        target_height = max(factor, _ceil_by_factor(resized_height * scale, factor))
        target_width = max(factor, _ceil_by_factor(resized_width * scale, factor))
    return target_height, target_width


def prepare_screenshot(
    image_bytes: bytes,
    *,
    factor: int = 32,
    quality: int = 75,
) -> tuple[str, tuple[int, int], tuple[int, int]]:
    if not image_bytes:
        raise ValueError("screenshot must not be empty")
    with Image.open(BytesIO(image_bytes)) as source:
        original_size = source.size
        target_height, target_width = smart_resize(
            source.height,
            source.width,
            factor=factor,
        )
        image = source.convert("RGB").resize((target_width, target_height))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=max(1, min(95, quality)))
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return encoded, original_size, (target_width, target_height)
