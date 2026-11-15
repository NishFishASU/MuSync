"""python -m musync — run the MuSync API + UI."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _ensure_path() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))


def main() -> None:
    _ensure_path()
    import uvicorn

    host = os.environ.get("MUSYNC_HOST", "0.0.0.0")
    port = int(os.environ.get("MUSYNC_PORT", "8080"))
    reload = os.environ.get("MUSYNC_RELOAD", "").lower() in {"1", "true", "yes"}

    uvicorn.run(
        "musync.app:app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    main()
