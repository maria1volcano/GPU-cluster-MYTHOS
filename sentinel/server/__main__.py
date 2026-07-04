"""Run with: python -m sentinel.server  or  uvicorn sentinel.server.app:app --reload --port 8000"""
from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run("sentinel.server.app:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
