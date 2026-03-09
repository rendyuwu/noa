from fastapi import FastAPI

from noa_api.api.router import api_router
from noa_api.core.logging import configure_logging


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Project NOA API")
    app.include_router(api_router)
    return app


app = create_app()
