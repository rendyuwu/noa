"""FastAPI application factory.

Skeleton per T3. `/health` is the only live route (V51). The MCP app is built here
but not mounted yet — mounting requires the `TokenVerifier` from T11, and mounting
an unauthenticated `/mcp` would breach V1. T13 does the mount plus lifespan
combination.
"""

from fastapi import FastAPI

from noa_api import __version__

TITLE = "NOA API"


def create_app() -> FastAPI:
    """Build the FastAPI app.

    Router sets land later: `/auth` + `/admin` (T8-T9, T51-T55),
    `/action-requests` (T37), `/mcp` (T13).
    """
    app = FastAPI(title=TITLE, version=__version__)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe (V51)."""
        return {"status": "ok"}

    return app


app = create_app()
