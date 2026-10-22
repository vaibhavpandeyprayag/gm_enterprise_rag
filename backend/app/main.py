"""FastAPI application factory for the General Purpose RAG backend.

Run from the `backend/` directory:
    uvicorn app.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth, banking, chat, conversations, files
from app.config import FRONTEND_DIST, get_settings
from app.services import mongo, reranker


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: load the reranker once, connect to Atlas, ensure indexes."""
    # The cross-encoder is expensive to load - do it once, before serving
    # requests. A failure is tolerated: retrieval falls back to vector order.
    reranker.warm_up()
    ok = mongo.ping()
    print(f"[startup] MongoDB ping: {'ok' if ok else 'FAILED (check MONGODB_URI/network)'}")
    if ok:
        mongo.ensure_indexes()
        mongo.ensure_vector_index()
    yield
    mongo.get_client().close()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="General Purpose RAG",
        description="Document ingestion + RAG chat over your files. MongoDB Atlas, Groq, Jina.",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- API routers ------------------------------------------------------
    app.include_router(auth.router)
    app.include_router(files.router)
    app.include_router(conversations.router)
    app.include_router(chat.router)
    app.include_router(banking.router)

    @app.get("/api/health", tags=["health"])
    def health() -> JSONResponse:
        """Liveness + config summary (no secrets)."""
        mongo_ok = mongo.ping()
        return JSONResponse(
            {
                "status": "ok" if mongo_ok else "degraded",
                "mongo": "ok" if mongo_ok else "unreachable",
                "llm": "groq",
                "llm_model": settings.groq_model,
                "embedding": "jina",
                "embedding_model": settings.jina_model,
                "embedding_dim": settings.embedding_dim,
                "supported_types": list(settings.supported_types),
                "auth": {
                    "session_ttl_hours": settings.session_ttl_hours,
                    "cookie_secure": settings.session_cookie_secure,
                },
            }
        )

    # ---- Serve the built React frontend (production mode) ----------------
    # Mounted last so /api/* routes always win.
    if FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")

    return app


app = create_app()
