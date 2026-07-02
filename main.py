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
PLACEHOLDER = "REPLACE"


class Settings(BaseSettings):
    # App
    app_name: str = "SolGuard AI"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = True

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 2
    enable_docs: bool = True

    # Solana RPC
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    solana_rpc_fallback_url: str = "https://solana-api.projectserum.com"
    solana_commitment: str = "confirmed"
    solana_timeout_seconds: int = 10
    solana_max_retries: int = 3

    # Database
    database_url: str = "sqlite:///./solguard.db"
    db_pool_size: int = 5
    db_echo_sql: bool = False

    # Caching / Rate limiting
    redis_url: str = "redis://localhost:6379/0"
    rate_limit_per_minute: int = 60

    # AI / Risk engine
    ai_model: str = "basic-risk-engine"
    risk_score_threshold: int = 70

    # Telegram
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Logging
    log_level: str = "INFO"
    log_json: bool = False

    # CORS
    allowed_origins: str = "*"

    # Security
    secret_key: str = f"{PLACEHOLDER}_SECRET_KEY"
    admin_api_key: str = f"{PLACEHOLDER}_ADMIN_API_KEY"

    # Monitoring
    sentry_dsn: str = ""

    @property
    def cors_origins(self) -> list[str]:
        if self.allowed_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    class Config:
        env_file = ".env"


settings = Settings()

# Fail fast if placeholder secrets are used outside development
if settings.environment != "development":
    if PLACEHOLDER in settings.secret_key:
        raise RuntimeError("SECRET_KEY must be set to a real value outside development.")
    if PLACEHOLDER in settings.admin_api_key:
        raise RuntimeError("ADMIN_API_KEY must be set to a real value outside development.")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
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
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url="/redoc" if settings.enable_docs else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
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


def mask(value: str, keep: int = 4) -> str:
    """Mask secrets so they're safe to expose in a debug endpoint."""
    if not value or len(value) <= keep:
        return "**"
    return value[:keep] + "*" * (len(value) - keep)


@v1_router.get("/status")
def status_info():
    """Safe, masked snapshot of current config — useful for debugging deploys."""
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "solana_rpc_url": settings.solana_rpc_url,
        "solana_commitment": settings.solana_commitment,
        "database": settings.database_url.split("://")[0] + "://*",
        "ai_model": settings.ai_model,
        "risk_score_threshold": settings.risk_score_threshold,
        "telegram_enabled": settings.telegram_enabled,
        "rate_limit_per_minute": settings.rate_limit_per_minute,
        "secret_key": mask(settings.secret_key),
        "admin_api_key": mask(settings.admin_api_key),
    }


async def send_telegram_alert(message: str) -> None:
    """Fire-and-forget alert to Telegram, if configured. Fails silently (but logs)."""
    if not settings.telegram_enabled or not settings.telegram_bot_token:
        return
    import httpx

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {"chat_id": settings.telegram_chat_id, "text": message}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(url, json=payload)
    except Exception as exc:
        logger.warning(f"Telegram alert failed: {exc}")


app.include_router(v1_router)

# Keep unversioned root routes too, for backward compatibility
app.get("/", response_model=RootResponse)(home)
app.get("/health", response_model=HealthResponse)(health)


# ---------------------------------------------------------------------------
# Local dev entrypoint: python main.py
# ---------------------------------------------------------------------------
if _name_ == "_main_":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
