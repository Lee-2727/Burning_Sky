"""Desktop entry point. The desktop and web clients share the same runtime/core."""
from __future__ import annotations
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from engine import FireCloudApp

if __name__ == "__main__":
    FireCloudApp().mainloop()
