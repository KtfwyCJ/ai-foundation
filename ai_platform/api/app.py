from fastapi import FastAPI

from ai_platform.api.errors import register_error_handlers
from ai_platform.api.routes import chat, health


def create_app() -> FastAPI:
    app = FastAPI(title="AI Platform Gateway")
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(chat.router)
    return app


app = create_app()
