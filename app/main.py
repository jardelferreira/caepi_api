from pathlib import Path
import secrets

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api.v1.admin import admin_router, login_router
from app.api.v1.certificates import router as certificates_router
from app.api.v1.health import router as health_router
from app.api.v1.imports import router as imports_router
from app.api.v1.integrations import router as integrations_router
from app.api.v1.tasks import router as tasks_router
from app.core.config import settings
from app.security.auth import admin_auth_is_configured, require_admin_access

STATIC_DIR = Path(__file__).parent / "static"
ASSETS_DIR = Path(__file__).parent / "assets"
app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    description=(
        "API para consulta de Certificados de Aprovação de EPIs. "
        "Consultas de integração usam chave Bearer; operações administrativas exigem login."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
)

# Sem segredo configurado, a sessão usa um valor efêmero, mas a autenticação fica
# desabilitada por admin_auth_is_configured(); nunca há um segredo padrão conhecido.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.admin_session_secret or secrets.token_urlsafe(48),
    session_cookie="caepi_admin_session",
    max_age=8 * 60 * 60,
    same_site="strict",
    https_only=settings.admin_cookie_secure,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path.startswith(("/admin", "/login", "/dashboard", "/certificates", "/api/v1/admin")):
        response.headers["Cache-Control"] = "no-store"
    if settings.admin_cookie_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


app.include_router(health_router, prefix=settings.api_prefix)
app.include_router(login_router, prefix=settings.api_prefix)
app.include_router(admin_router, prefix=settings.api_prefix)
# Operações da dashboard: leitura operacional, importações e enfileiramento exigem sessão
# administrativa. require_admin_access também verifica o token CSRF em métodos mutáveis.
admin_api_dependencies = [Depends(require_admin_access)]
app.include_router(certificates_router, prefix=settings.api_prefix, dependencies=admin_api_dependencies)
app.include_router(imports_router, prefix=settings.api_prefix, dependencies=admin_api_dependencies)
app.include_router(tasks_router, prefix=settings.api_prefix, dependencies=admin_api_dependencies)
# Rotas públicas de integração ficam separadas e só aceitam chaves Bearer com escopos.
app.include_router(integrations_router, prefix=settings.api_prefix)


def _admin_page(request: Request, filename: str):
    if not admin_auth_is_configured():
        return RedirectResponse("/login?setup=required", status_code=303)
    if not request.session.get("admin_authenticated"):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(STATIC_DIR / filename, headers={"Cache-Control": "no-store"})


@app.get("/login", tags=["admin-pages"], include_in_schema=False)
def login_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "login.html", headers={"Cache-Control": "no-store"})


@app.get("/admin", tags=["admin-pages"], include_in_schema=False)
def admin_home(request: Request):
    return _admin_page(request, "admin-home.html")


@app.get("/admin/consult", tags=["admin-pages"], include_in_schema=False)
def admin_consult_page(request: Request):
    return _admin_page(request, "admin-consult.html")


@app.get("/admin/api-keys", tags=["admin-pages"], include_in_schema=False)
def admin_api_keys_page(request: Request):
    return _admin_page(request, "api-keys.html")


@app.get("/dashboard", tags=["admin-pages"], include_in_schema=False)
def dashboard(request: Request):
    return _admin_page(request, "dashboard.html")


@app.get("/certificates", tags=["admin-pages"], include_in_schema=False)
def certificates_page(request: Request):
    return _admin_page(request, "dashboard.html")


@app.get("/admin/sources", tags=["admin-pages"], include_in_schema=False)
def sources_page(request: Request):
    return _admin_page(request, "dashboard.html")


@app.get("/", tags=["meta"], include_in_schema=False)
def root():
    return RedirectResponse("/admin", status_code=303)


app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")
