"""Worker entrypoint (ARCHITECTURE.md §36: worker/worker.py). Run with: python worker.py"""

import asyncio

from app.main import main

if __name__ == "__main__":
    try:
        import uvloop  # faster event loop (libuv); optional
    except ImportError:
        asyncio.run(main())
    else:
        uvloop.run(main())
