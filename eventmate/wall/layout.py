"""Where a slot is drawn on the wall: a fixed table in code keyed by (layout_version, slot).

A section stores the layout version it was created under, so adding a version never moves a name that is already on the wall. Coordinates are
fractions of the wall's inscription area (0 to 1, origin top-left); the frontend maps them onto the left wall of the 3D room.
"""

from __future__ import annotations

from typing import Dict, Tuple

# layout_version -> (columns, margin). Rows follow from the section's capacity. Never edit an existing entry: add a new version.
LAYOUTS: Dict[int, Tuple[int, float]] = {
    1: (8, 0.04),
}
CURRENT_LAYOUT = 1


class UnknownLayout(ValueError):
    pass


def slot_position(layout_version: int, capacity: int, slot: int) -> Tuple[float, float]:
    """(u, v) of the centre of a slot. Raises for a layout this code does not know or a slot outside the section."""
    if layout_version not in LAYOUTS:
        raise UnknownLayout(f"layout version {layout_version} is not known to this build")
    if not 0 <= slot < capacity:
        raise ValueError("slot outside the section")
    cols, margin = LAYOUTS[layout_version]
    rows = -(-capacity // cols)
    col, row = slot % cols, slot // cols
    span = 1.0 - 2 * margin
    return round(margin + span * (col + 0.5) / cols, 4), round(margin + span * (row + 0.5) / rows, 4)
