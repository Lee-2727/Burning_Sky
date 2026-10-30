# -*- coding: utf-8 -*-
"""Compatibility launcher for Burning Sky v1.4.67 desktop client.

The actual implementation is shared in the project-root engine.py and
burning_sky/algorithm.py; this launcher keeps the familiar desktop filename.
"""
from __future__ import annotations
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from engine import FireCloudApp

if __name__ == "__main__":
    FireCloudApp().mainloop()
