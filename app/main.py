\
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api.v1.certificates import router as certificates_router
from app.api.v1.health import router as health_router
from app.api.v1.tasks import router as tasks_router
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
app.include_router(tasks_router, prefix=settings.api_prefix)


@app.get("/dashboard", tags=["dashboard"], include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "dashboard.html")


@app.get("/", tags=["meta"])
def root() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "version": app.version,
        "docs": "/docs",
        "dashboard": "/dashboard",
        "status": "running",
    }
