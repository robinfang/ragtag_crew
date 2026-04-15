from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ragtag_crew.zhidu_client import main


if __name__ == "__main__":
    raise SystemExit(main())
