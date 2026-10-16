"""Deterministic full-image placement; never crops or stretches a source."""
import math


def contain_box(source_w, source_h, frame_x, frame_y, frame_w, frame_h):
    """Return centered (x, y, width, height), in the caller's coordinate units."""
    values = [source_w, source_h, frame_x, frame_y, frame_w, frame_h]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in values):
        raise ValueError("All dimensions and coordinates must be finite numbers")
    if min(source_w, source_h, frame_w, frame_h) <= 0:
        raise ValueError("Source and frame dimensions must be positive")
    scale = min(frame_w / source_w, frame_h / source_h)
    width, height = source_w * scale, source_h * scale
    return (frame_x + (frame_w - width) / 2,
            frame_y + (frame_h - height) / 2, width, height)
