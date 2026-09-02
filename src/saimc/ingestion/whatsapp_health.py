"""Estado de la sesión de WAHA, para que una ingesta detenida se vea.

Cierra el hueco **N-02** (`docs/requisitos.md` §02): la sesión cayó a `FAILED`
una vez y toda la ingesta automática paró en silencio, porque no había nada
que lo dijera. La cola simplemente dejaba de crecer, que es la peor forma de
fallar: sin síntoma.

Mismo reparto que `exchange_rate.eltoque`: `parse_status` es puro y se prueba
sin red, `session_health` hace la llamada y degrada. Y mismo criterio que
`whatsapp_webhook` para la forma de la respuesta — se fija contra lo que
documenta WAHA, no contra una instancia real. Es una calibración razonada: si
la instancia devuelve otra cosa, esto responde «desconocido» en vez de
afirmar que anda o que se cayó.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

# Estados que documenta WAHA, normalizados a minúscula. Lo que no esté acá cae
# en `unknown` a propósito: inventar un significado para un estado nuevo es
# peor que declarar que no se sabe.
_KNOWN_STATUSES: dict[str, str] = {
    "working": "working",
    "failed": "failed",
    "stopped": "stopped",
    "starting": "starting",
    "scan_qr_code": "scan_qr",
}

# El único estado en el que la ingesta automática realmente está entrando.
_HEALTHY = "working"

#: Qué decirle al operador de cada estado. Sin esto el panel muestra una
#: palabra en inglés y lo deja adivinando qué hacer.
STATUS_LABELS: dict[str, str] = {
    "working": "conectado",
    "failed": "caído",
    "stopped": "detenido",
    "starting": "arrancando",
    "scan_qr": "esperando el QR",
    "unreachable": "no responde",
    "unknown": "desconocido",
}

STATUS_HINTS: dict[str, str] = {
    "failed": "La sesión murió. Reiniciala en WAHA: nada entra mientras tanto.",
    "stopped": "La sesión está detenida. Arrancala en WAHA.",
    "starting": "Arrancando. Si queda así, revisá WAHA.",
    "scan_qr": "Hay que escanear el QR de nuevo desde WAHA.",
    "unreachable": "WAHA no contesta. Puede estar apagado o mal configurado.",
    "unknown": "Respondió algo que no se reconoce. Revisá WAHA a mano.",
}


@dataclass(frozen=True)
class SessionHealth:
    """Lo que se sabe de la sesión de WAHA en este momento."""

    # `False` cuando falta `waha_base_url` o `waha_api_key`: no es que esté
    # caída, es que nunca se configuró. Distinguirlo evita alarmar a quien
    # todavía no usa canales automatizados.
    configured: bool
    status: str

    @property
    def healthy(self) -> bool:
        return self.status == _HEALTHY

    @property
    def label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def hint(self) -> str:
        return STATUS_HINTS.get(self.status, "")


def parse_status(body: Any) -> str:
    """Estado normalizado a partir del cuerpo de la respuesta, o `unknown`.

    Puro, sin red. Acepta el objeto de sesión directo y también una lista
    —`/api/sessions` devuelve varias—, tomando la primera: el llamador ya
    consultó por la sesión que le interesa.
    """
    if isinstance(body, list):
        body = body[0] if body else None
    if not isinstance(body, dict):
        return "unknown"

    raw = body.get("status")
    if not isinstance(raw, str):
        return "unknown"
    return _KNOWN_STATUSES.get(raw.strip().lower(), "unknown")


def session_health(
    *,
    base_url: str | None,
    api_key: str | None,
    session: str,
    timeout: float = 5.0,
    transport: httpx.BaseTransport | None = None,
) -> SessionHealth:
    """Consulta el estado de la sesión. Nunca levanta.

    Cualquier falla —sin configurar, WAHA apagado, timeout, status HTTP de
    error, cuerpo raro— termina en un `SessionHealth` que lo declara. Mismo
    contrato que `eltoque.fetch_rate`: esta llamada vive dentro de la carga de
    `/control` y no puede tumbarla.

    El timeout es corto a propósito. WAHA corre en el mismo VPS, así que si no
    contesta en unos segundos no va a contestar; esperar más solo demora el
    panel.
    """
    if not base_url or not api_key:
        return SessionHealth(configured=False, status="unknown")

    url = f"{base_url.rstrip('/')}/api/sessions/{session}"
    try:
        with httpx.Client(transport=transport, timeout=timeout) as client:
            response = client.get(url, headers={"X-Api-Key": api_key})
    except httpx.HTTPError:
        return SessionHealth(configured=True, status="unreachable")

    if response.status_code != 200:
        return SessionHealth(configured=True, status="unreachable")

    try:
        body = response.json()
    except ValueError:
        return SessionHealth(configured=True, status="unknown")

    return SessionHealth(configured=True, status=parse_status(body))
