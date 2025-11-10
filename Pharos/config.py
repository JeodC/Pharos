#!/usr/bin/env python3
"""
Controller layout → button mapping
"""
import os
from typing import TypedDict

# ----------------------------------------------------------------------
# Button colours (used by UI)
# ----------------------------------------------------------------------
color_btn_a = "#2f843e"
color_btn_b = "#ad3c3c"
color_btn_x = "#3b80aa"
color_btn_y = "#d3b948"
color_btn_shoulder = "#383838"

# ----------------------------------------------------------------------
# TypedDict definitions – only the fields that are actually read
# ----------------------------------------------------------------------
class Button(TypedDict):
    key: str          # SDL key name (e.g. "A")
    btn: str          # Label shown on-screen (e.g. "A")
    color: str        # Hex colour

class ButtonConfig(TypedDict):
    a: Button
    b: Button
    x: Button
    y: Button
    l1: Button
    r1: Button

# ----------------------------------------------------------------------
# Hard-coded layouts
# ----------------------------------------------------------------------
BUTTON_CONFIGS: dict[str, ButtonConfig] = {
    "nintendo": {
        "a": {"key": "A", "btn": "A", "color": color_btn_a},
        "b": {"key": "B", "btn": "B", "color": color_btn_b},
        "x": {"key": "X", "btn": "X", "color": color_btn_x},
        "y": {"key": "Y", "btn": "Y", "color": color_btn_y},
        "l1": {"key": "L1", "btn": "L1", "color": color_btn_shoulder},
        "r1": {"key": "R1", "btn": "R1", "color": color_btn_shoulder},
    },
    "xbox": {
        "a": {"key": "B", "btn": "A", "color": color_btn_a},
        "b": {"key": "A", "btn": "B", "color": color_btn_b},
        "x": {"key": "Y", "btn": "X", "color": color_btn_x},
        "y": {"key": "X", "btn": "Y", "color": color_btn_y},
        "l1": {"key": "L1", "btn": "L1", "color": color_btn_shoulder},
        "r1": {"key": "R1", "btn": "R1", "color": color_btn_shoulder},
    },
}

# ----------------------------------------------------------------------
# Public API – only the function that Pharos actually calls
# ----------------------------------------------------------------------
CONTROLLER_LAYOUT = os.getenv("CONTROLLER_LAYOUT", "nintendo").lower()

def get_controller_layout() -> ButtonConfig:
    """Return the button mapping for the current layout."""
    if CONTROLLER_LAYOUT not in BUTTON_CONFIGS:
        raise ValueError(f"Invalid controller layout: {CONTROLLER_LAYOUT}")
    return BUTTON_CONFIGS[CONTROLLER_LAYOUT]

# ----------------------------------------------------------------------
# Data containers – only the fields that are used elsewhere
# ----------------------------------------------------------------------
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class Port:
    name: str
    title: str
    desc: str = "Missing description"
    download_url: str = ""
    image_path: Optional[str] = None
    date_updated: Optional[str] = None
    size: Optional[int] = None
    md5: Optional[str] = None

@dataclass
class Repository:
    name: str
    url: str
    images_dir: Optional[str] = None
    images_zip_url: Optional[str] = None
    ports: List[Port] = field(default_factory=list)
    bottles: List[Port] = field(default_factory=list)