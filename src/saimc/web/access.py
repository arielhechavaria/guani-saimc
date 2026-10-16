"""Acceso al sitio público con usuario y contraseña (2026-10-13).

El operador pasó el enlace a algunas personas para recibir comentarios y no
quiere que nadie más entre. Con `SAIMC_SITE_GATE_ENABLED` encendido, la capa
pública (`/`, `/precios`, producto, anuncio, el CSV y las páginas de texto)
manda a un formulario; entrando, una cookie firmada deja pasar por
`SAIMC_SITE_ACCESS_DAYS` días.

Qué NO toca:

- Las rutas del operador (`/curation`, `/control`, `/catalog`, `/marketing`,
  `/docs`, `/evidence`) siguen con su HTTP Basic (`web/auth.py`).
- El webhook de WAHA sigue con su HMAC, y `/static` sigue abierto.

Las credenciales son las del operador (`SAIMC_CURATION_USERNAME` y
`SAIMC_CURATION_PASSWORD`). Ojo: quien las tenga también abre la herramienta
del operador. Para dar acceso solo al sitio, `SAIMC_SITE_PASSWORD` fija otra
contraseña que vale únicamente aquí; la del operador sigue valiendo.

La cookie lleva su vencimiento y una firma HMAC con un secreto derivado de las
contraseñas: cambiar cualquiera de las dos cierra todas las sesiones.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from urllib.parse import quote

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.middleware.base import RequestResponseEndpoint

from saimc.config import Settings, get_settings
from saimc.web.site_pages import CONTACT_PATH, SITE_PAGES

COOKIE = "saimc_acceso"
LOGIN_PATH = "/acceso"

#: Intentos fallidos que se toleran por origen en la ventana, y su duración.
MAX_FAILURES = 5
WINDOW_SECONDS = 10 * 60

_NO_STORE = {"Cache-Control": "no-store"}

router = APIRouter()

# origen -> instantes de los intentos fallidos dentro de la ventana. En memoria:
# un reinicio lo vacía, que es aceptable para frenar a quien adivina claves.
_failures: dict[str, list[float]] = {}

_PUBLIC_EXACT = frozenset({"/", "/precios", "/datos.csv", CONTACT_PATH, *(page.path for page in SITE_PAGES)})
_PUBLIC_PREFIXES = ("/producto/", "/anuncio/")


def reset_attempts() -> None:
    """Vacía el conteo de intentos fallidos (los tests lo usan)."""
    _failures.clear()


def is_gated(path: str) -> bool:
    """¿Es una ruta de la capa pública, la que el acceso protege?"""
    return path in _PUBLIC_EXACT or path.startswith(_PUBLIC_PREFIXES)


def _secret(settings: Settings) -> bytes:
    material = f"saimc-acceso|{settings.curation_password}|{settings.site_password or ''}"
    return hashlib.sha256(material.encode()).digest()


def _sign(settings: Settings, expires: int) -> str:
    return hmac.new(_secret(settings), str(expires).encode(), hashlib.sha256).hexdigest()


def make_token(settings: Settings, *, now: float | None = None) -> str:
    """`vencimiento.firma`: lo que vale como cookie de acceso."""
    expires = int((now if now is not None else time.time()) + settings.site_access_days * 86400)
    return f"{expires}.{_sign(settings, expires)}"


def valid_token(settings: Settings, token: str | None, *, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    raw_expires, _, signature = token.partition(".")
    if not raw_expires.isdigit():
        return False
    expires = int(raw_expires)
    if expires < (now if now is not None else time.time()):
        return False
    return hmac.compare_digest(signature, _sign(settings, expires))


def credentials_ok(settings: Settings, username: str, password: str) -> bool:
    """Usuario y contraseña del operador, o la contraseña propia del sitio."""
    if not settings.curation_password:
        return False
    user_ok = secrets.compare_digest(username.encode(), settings.curation_username.encode())
    operator_ok = secrets.compare_digest(password.encode(), settings.curation_password.encode())
    site_ok = bool(settings.site_password) and secrets.compare_digest(
        password.encode(), (settings.site_password or "").encode()
    )
    return user_ok and (operator_ok or site_ok)


def safe_next(raw: str | None) -> str:
    """Solo rutas del propio sitio: nunca un esquema, un host ni `//`."""
    if not raw or not raw.startswith("/") or raw.startswith("//") or "\\" in raw:
        return "/"
    return raw


def _origin(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "desconocido"


def _recent_failures(origin: str, now: float) -> list[float]:
    kept = [t for t in _failures.get(origin, []) if now - t < WINDOW_SECONDS]
    _failures[origin] = kept
    return kept


async def require_access(request: Request, call_next: RequestResponseEndpoint) -> Response:
    """Middleware: manda al formulario a quien pide la capa pública sin acceso."""
    settings = get_settings()
    if not settings.site_gate_enabled or not is_gated(request.url.path):
        return await call_next(request)
    if not settings.curation_password:
        # Encendido pero sin contraseña con qué comparar: se cierra, no se abre.
        return Response("Acceso privado: falta configurar la contraseña.", 503, _NO_STORE)
    if valid_token(settings, request.cookies.get(COOKIE)):
        return await call_next(request)
    if request.headers.get("hx-request") is not None:
        return Response(status_code=401, headers={**_NO_STORE, "HX-Redirect": LOGIN_PATH})
    if request.method not in ("GET", "HEAD"):
        return Response("Acceso privado", 401, _NO_STORE)
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"{LOGIN_PATH}?next={quote(target, safe='')}", status_code=303, headers=_NO_STORE)


def _render_form(
    request: Request, *, next_path: str, error: str | None = None, status_code: int = 200
) -> HTMLResponse:
    response: HTMLResponse = request.app.state.templates.TemplateResponse(
        request,
        "public_access.html",
        {"next_path": next_path, "error": error},
        status_code=status_code,
    )
    response.headers.update(_NO_STORE)
    return response


@router.get(LOGIN_PATH, response_class=HTMLResponse)
async def login_form(
    request: Request, next_: str | None = Query(None, alias="next")
) -> HTMLResponse:
    return _render_form(request, next_path=safe_next(next_))


@router.post(LOGIN_PATH)
async def login(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next_: str | None = Form(None, alias="next"),
) -> Response:
    settings = get_settings()
    now = time.time()
    origin = _origin(request)
    next_path = safe_next(next_)
    if len(_recent_failures(origin, now)) >= MAX_FAILURES:
        return _render_form(
            request,
            next_path=next_path,
            error="Demasiados intentos. Espera unos minutos y vuelve a probar.",
            status_code=429,
        )
    if not credentials_ok(settings, username, password):
        _failures.setdefault(origin, []).append(now)
        return _render_form(
            request,
            next_path=next_path,
            error="Usuario o contraseña incorrectos.",
            status_code=401,
        )
    _failures.pop(origin, None)
    response = RedirectResponse(next_path, status_code=303, headers=_NO_STORE)
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        COOKIE,
        make_token(settings, now=now),
        max_age=settings.site_access_days * 86400,
        httponly=True,
        samesite="lax",
        secure=secure,
    )
    return response
