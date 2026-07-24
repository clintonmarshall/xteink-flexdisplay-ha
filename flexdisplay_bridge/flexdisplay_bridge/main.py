from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "flexdisplay_bridge.app:app",
        host=os.getenv("FLEXDISPLAY_HOST", "0.0.0.0"),
        port=int(os.getenv("FLEXDISPLAY_PORT", "8099")),
        reload=False,
    )


if __name__ == "__main__":
    main()
