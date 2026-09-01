"""Serve the bundled single-page frontend without shadowing backend routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response

_RESERVED_PREFIXES = ("/api", "/health", "/docs", "/redoc", "/openapi.json")
_CACHEABLE_ASSET_PREFIX = "/assets/"
_FRONTEND_UNAVAILABLE_DETAIL = {
    "code": "frontend_unavailable",
    "message": "Bundled frontend assets are unavailable; install a release wheel built with the frontend assets.",
}


def bundled_frontend_directory() -> Path:
    """Return the directory into which the release build embeds Vite output."""
    return Path(__file__).with_name("frontend")


class FrontendApp:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = (directory or bundled_frontend_directory()).resolve()

    def is_reserved(self, path: str) -> bool:
        return any(
            path == prefix or path.startswith(f"{prefix}/")
            for prefix in _RESERVED_PREFIXES
        )

    def _file_for(self, path: str) -> Path | None:
        candidate = (self.directory / path.lstrip("/")).resolve()
        try:
            candidate.relative_to(self.directory)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def _require_index(self) -> Path:
        index = self.directory / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=503, detail=_FRONTEND_UNAVAILABLE_DETAIL)
        return index

    @staticmethod
    def _file_response(
        path: Path, *, immutable: bool = False, revalidate: bool = False
    ) -> FileResponse:
        headers = (
            {"Cache-Control": "public, max-age=31536000, immutable"}
            if immutable
            else {"Cache-Control": "no-cache"}
            if revalidate
            else {}
        )
        return FileResponse(path, headers=headers)

    async def handle(self, request: Request, path: str) -> Response:
        if self.is_reserved(request.url.path):
            raise HTTPException(status_code=404, detail="Not Found")
        if request.method not in {"GET", "HEAD"}:
            raise HTTPException(status_code=404, detail="Not Found")

        file_path = self._file_for(request.url.path)
        if file_path is not None:
            return self._file_response(
                file_path,
                immutable=request.url.path.startswith(_CACHEABLE_ASSET_PREFIX),
            )

        if request.url.path.startswith(_CACHEABLE_ASSET_PREFIX) or Path(path).suffix:
            raise HTTPException(status_code=404, detail="Not Found")

        return self._file_response(self._require_index(), revalidate=True)
