SolGuard AI — FastAPI Backend
Advanced/production-ready structure with logging, CORS, versioning,
structured responses, error handling, and health/status monitoring.
"""

import logging
import time
from datetime import datetime, timezone

from fastapi import FastAPI, APIRouter, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pydantic_settings import BaseSettings


# ---------------------------------------------------------------------------
# Settings (env-driven config, so you don't hardcode values)
# ---------------------------------------------------------------------------
class Settings(BaseSettings):
    app_name: str = "SolGuard AI"
    app_version: str = "0.1.0"
    environment: str = "development"
    allowed_origins: list[str] = ["*"]

    class Config:
        env_file = ".env"


settings = Settings()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("solguard")

# ---------------------------------------------------------------------------
# App instance
# ---------------------------------------------------------------------------
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Backend API for SolGuard AI",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

START_TIME = time.time()


# ---------------------------------------------------------------------------
# Request logging + timing middleware
# ---------------------------------------------------------------------------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        f"{request.method} {request.url.path} "
        f"-> {response.status_code} ({duration_ms:.2f}ms)"
    )
    response.headers["X-Process-Time-ms"] = f"{duration_ms:.2f}"
    return response


# ---------------------------------------------------------------------------
# Global exception handler (never leak raw tracebacks to clients)
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled error on {request.url.path}: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "internal_server_error", "detail": "Something went wrong."},
    )


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class RootResponse(BaseModel):
    project: str
    status: str
    version: str
    environment: str


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    uptime_seconds: float


# ---------------------------------------------------------------------------
# Versioned router (v1) — scales cleanly as you add real features
# ---------------------------------------------------------------------------
v1_router = APIRouter(prefix="/api/v1", tags=["v1"])


@v1_router.get("/", response_model=RootResponse)
def home():
    return RootResponse(
        project=settings.app_name,
        status="MVP Under Development",
        version=settings.app_version,
        environment=settings.environment,
    )


@v1_router.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(
        status="ok",
        timestamp=datetime.now(timezone.utc).isoformat(),
        uptime_seconds=round(time.time() - START_TIME, 2),
    )


app.include_router(v1_router)

# Keep unversioned root routes too, for backward compatibility
app.get("/", response_model=RootResponse)(home)
app.get("/health", response_model=HealthResponse)(health)


# ---------------------------------------------------------------------------
# Local dev entrypoint: python main.py
# ---------------------------------------------------------------------------
if _name_ == "_main_":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
