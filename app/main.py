from fastapi import FastAPI

from app.api.v1.certificates import router as certificates_router
from app.api.v1.health import router as health_router
from app.core.config import settings

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="API para consulta e enriquecimento de Certificados de Aprovação (CA) de EPIs.",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.include_router(health_router, prefix=settings.api_prefix)
app.include_router(certificates_router, prefix=settings.api_prefix)


@app.get("/", tags=["meta"])
def root() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "version": app.version,
        "docs": "/docs",
        "status": "running",
    }

