"""Mount the built Web UI (docs/app/) at /app on the FastAPI app.

The source for this bundle lives under `web/` and is built via `vite
build` into `docs/app/`. Both the source and the built output are
committed so:
  - End users who `pip install devicehands` never need node to use the UI
  - GitHub Pages can serve the same `docs/app/` directly
  - The wheel carries a copy as `devicehands/_resources/web/`, so an
    installed hub serves the UI without any checkout around

Where the files are read from is decided by infra.resources: the
installed copy first, then the checkout's `docs/app/`. If neither has an
index.html, the mount is skipped and /app returns 404 — the CLI and MCP
paths are unaffected.

SPA fallback (DEBT-014, 2026-04-29): TanStack Router uses HTML5
history mode, so direct hits to `/app/dashboard` / `/app/inspect` /
etc. on browser refresh or shared deep-links must serve `index.html`
to let the client-side router resolve the route. `SPAStaticFiles`
extends StaticFiles to do this without rewriting real 404s for
missing assets.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, Response
from starlette.types import Scope

from devicehands.infra.resources import WEB, resource_dir


class SPAStaticFiles(StaticFiles):
    """StaticFiles that serves index.html for unknown SPA route paths.

    Two mechanisms cover orthogonal paths and do NOT overlap:
      - `html=True` (the StaticFiles flag) handles the bare `/app/` →
        `index.html` mapping (directory index lookup).
      - `SPAStaticFiles.get_response` handles `/app/<route>` deep links
        like `/app/dashboard`, `/app/inspect`, `/app/sessions/abc-123`.

    Why a custom subclass and not a FastAPI catch-all route: a sibling
    catch-all `@app.get("/app/{path:path}")` would have to be ordered
    relative to `app.mount("/app", ...)`, and any ordering bug routes
    real assets (`/app/assets/index-XYZ.js`) through the HTML fallback,
    silently breaking caching and content types. Subclassing keeps the
    fallback inside the same StaticFiles handler, so asset lookups and
    SPA fallback share one path resolution pass.

    Heuristic: a missing path is treated as an SPA route iff its last
    segment has no `.`. Anything with a dot (`index-XYZ.js`,
    `favicon.ico`, `bundle.min.js.map`) propagates the 404 unchanged
    so broken asset URLs surface as real errors instead of degenerating
    into a confusing white-page render.

    **Cross-repo invariant**: TanStack Router routes (`web/src/router.tsx`)
    MUST NOT contain `.` in any path segment, or this heuristic
    misclassifies them as asset paths and 404s. See
    `.claude/knowledge/architecture.md` 关键不变量 段。

    Error propagation: only 404 from a missing path gets rewritten;
    401 (PermissionError), 405 (non-GET/HEAD), and OSError propagate
    unchanged.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as e:
            if e.status_code != 404:
                raise
            tail = path.rsplit("/", 1)[-1]
            if "." in tail:
                # Looks like a missing asset — let the real 404 surface.
                raise
            return FileResponse(Path(str(self.directory)) / "index.html")


def ui_dir() -> Path | None:
    """The built web UI: the copy inside the installed package, else the
    checkout's docs/app (infra.resources). None when it has no index.html."""
    app_dir = resource_dir(WEB)
    if app_dir is None or not (app_dir / "index.html").is_file():
        return None
    return app_dir


def mount_ui(app: FastAPI, *, url_prefix: str = "/app") -> bool:
    """Mount the React bundle. Returns True if a bundle was found."""
    app_dir = ui_dir()
    if app_dir is None:
        return False
    app.mount(
        url_prefix,
        SPAStaticFiles(directory=str(app_dir), html=True),
        name="dhands-ui",
    )
    return True
