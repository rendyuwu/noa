from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from noa_api.api.error_handling import install_error_handling
from noa_api.api.router import api_router
from noa_api.core.config import Settings, settings
from noa_api.core.logging import configure_logging


def create_app(app_settings: Settings = settings) -> FastAPI:
    configure_logging()
    app = FastAPI(title="Project NOA API")
    if app_settings.api_cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=app_settings.api_cors_allowed_origins,
            allow_credentials=app_settings.api_cors_allow_credentials,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    install_error_handling(app)
    app.include_router(api_router)
    return app


app = create_app()
