"""The FastAPI application.

Server-rendered HTML with HTMX for partial updates. No build step, no API client
layer, no bundler — the whole surface is Jinja templates and a stylesheet, which for
a dashboard of tables and forms costs nothing and removes a lot of moving parts.

Route handlers stay thin. Everything they do lives in `landed.services`, so the same
operations are reachable from the CLI and the evaluation harness without going
through HTTP.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from landed.config import settings
from landed.services.projects import ProjectNotFound
from landed.web.routes import auth, projects
from landed.web.templating import templates

WEB_ROOT = Path(__file__).parent


def create_app() -> FastAPI:
    app = FastAPI(
        title="Landed",
        description="Quotation normalization and landed-cost intelligence.",
        docs_url="/api/docs",
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings().secret_key,
        max_age=settings().session_max_age_seconds,
        same_site="lax",
        https_only=False,  # the demo runs over http; set true behind TLS
    )
    app.mount(
        "/static", StaticFiles(directory=WEB_ROOT / "static"), name="static"
    )
    app.include_router(auth.router)
    app.include_router(projects.router)

    @app.exception_handler(ProjectNotFound)
    async def handle_not_found(request: Request, exc: ProjectNotFound):
        """Missing and forbidden render identically.

        A distinguishable "forbidden" tells a prober which project ids exist.
        """
        return templates.TemplateResponse(
            request,
            "error.html",
            {"status": 404, "detail": "That project does not exist."},
            status_code=status.HTTP_404_NOT_FOUND,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException):
        """Redirects carry a Location header; everything else renders a page.

        An HTML app returning a JSON error body is a dead end for the person looking
        at it.
        """
        location = (exc.headers or {}).get("Location")
        if location:
            return RedirectResponse(location, status_code=exc.status_code)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"status": exc.status_code, "detail": exc.detail},
            status_code=exc.status_code,
        )

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()


__all__ = ["app", "create_app", "HTMLResponse"]
