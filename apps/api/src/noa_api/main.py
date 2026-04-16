import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from noa_api.api.error_handling import install_error_handling
from noa_api.api.router import api_router
from noa_api.core.config import Settings, settings
from noa_api.core.logging import configure_logging
from noa_api.core.prompts.loader import load_system_prompt
from noa_api.core.telemetry import create_telemetry_recorder
from noa_api.core.agent.runner import require_llm_api_key


logger = logging.getLogger(__name__)


def create_app(app_settings: Settings = settings) -> FastAPI:
    configure_logging()
    require_llm_api_key(app_settings)
    prompt = load_system_prompt(app_settings)
    logger.info(
        "llm_system_prompt_loaded",
        extra={
            "prompt_fingerprint": prompt.fingerprint,
            "prompt_source_count": len(prompt.sources),
            "prompt_sources": list(prompt.sources),
        },
    )
    telemetry = create_telemetry_recorder(app_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            telemetry.shutdown()

    app = FastAPI(title="Project NOA API", lifespan=lifespan)
    app.state.settings = app_settings
    app.state.telemetry = telemetry
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
