"""Serve the built panel (web/dist) from the API, with SPA fallback.

Mounted last, so /api, /webhooks and /healthz always win. Unknown /api and
/webhooks paths stay JSON 404s instead of falling back to index.html.
"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

IMMUTABLE = "public, max-age=31536000, immutable"  # Vite hashes everything under /assets
NO_CACHE = "no-cache"
BACKEND_PREFIXES = ("api", "webhooks")


def mount_web(app: FastAPI, dist: Path | None) -> None:
    """No-op when there is no build (dev, tests, a bare API image)."""
    if dist is None:
        return
    root = dist.resolve()
    index = root / "index.html"
    if not index.is_file():
        return

    @app.get("/{path:path}", include_in_schema=False)
    def panel(path: str) -> Response:
        if path.split("/", 1)[0] in BACKEND_PREFIXES:
            raise HTTPException(404, "não encontrado")
        target = (root / path).resolve()
        if path and target.is_relative_to(root) and target.is_file():
            hashed = target.relative_to(root).parts[0] == "assets"
            return FileResponse(target, headers={"Cache-Control": IMMUTABLE if hashed else NO_CACHE})
        return FileResponse(index, headers={"Cache-Control": NO_CACHE})
