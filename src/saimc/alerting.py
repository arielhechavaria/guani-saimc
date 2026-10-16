"""Avisos al celular: el sistema le escribe al operador cuando algo lo necesita.

El objetivo es que el operador no abra `/control` para saber si todo anda. Un
temporizador de systemd corre este comando cada 15 minutos; si algo de la
franja de estado (`web/strip_loader.py`) está en ámbar o rojo, o el carril 1
tiene el freno puesto, manda un mensaje por Telegram. Una vez al día manda un
«todo en orden» con los números: si ese mensaje deja de llegar, lo roto es el
propio sistema de avisos.

El bot solo envía. No recibe mensajes ni captura anuncios (decisión cerrada 2).

Qué se avisa y cuándo es la parte pura (`decide`): un problema nuevo avisa una
vez, se recuerda a las 24 h, avisa si empeora y avisa cuando se resuelve.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from saimc.config import Settings, get_settings
from saimc.db import session_scope
from saimc.progress import Last24h, last_24h
from saimc.validation import lane1
from saimc.web.strip_loader import load_strip

REMIND_AFTER = timedelta(hours=24)
# Hora UTC desde la cual sale el resumen diario (09:00 en La Habana).
HEARTBEAT_HOUR_UTC = 13
_RANK = {"warn": 1, "down": 2}
_ICON = {"warn": "🟡", "down": "🔴"}
_HEARTBEAT_KEY = "_heartbeat"
_TELEGRAM_API = "https://api.telegram.org"


@dataclass(frozen=True)
class Alert:
    key: str
    level: str  # "warn" | "down"
    text: str


def decide(
    current: list[Alert],
    state: dict[str, dict[str, str]],
    *,
    now: datetime,
) -> tuple[list[str], dict[str, dict[str, str]]]:
    """Qué mandar ahora y cuál es el estado nuevo. Pura: no manda ni lee nada.

    `state` guarda, por problema, el nivel con que se avisó, cuándo y su texto
    (para poder decir «recuperado» cuando ya no está en `current`).
    """
    nuevo: dict[str, dict[str, str]] = {k: v for k, v in state.items() if k.startswith("_")}
    lineas: list[str] = []
    for alerta in current:
        previo = state.get(alerta.key)
        avisar = (
            previo is None
            or _RANK[alerta.level] > _RANK.get(previo["level"], 0)
            or now - datetime.fromisoformat(previo["sent"]) >= REMIND_AFTER
        )
        if avisar:
            lineas.append(f"{_ICON[alerta.level]} {alerta.text}")
            nuevo[alerta.key] = {
                "level": alerta.level,
                "sent": now.isoformat(),
                "text": alerta.text,
            }
        elif previo is not None:
            # Sin mensaje: se conserva cuándo se avisó, con el nivel de ahora
            # solo si no mejoró (mejorar no baja la vara de «empeoró»).
            nuevo[alerta.key] = dict(previo)
    vigentes = {a.key for a in current}
    for clave, previo in state.items():
        if clave.startswith("_") or clave in vigentes:
            continue
        lineas.append(f"✅ Resuelto: {previo['text']}")
    mensajes = ["\n".join(lineas)] if lineas else []
    return mensajes, nuevo


def send_telegram(token: str, chat_id: str, text: str, *, client: httpx.Client | None = None) -> bool:
    """Manda un mensaje. `False` si el bot lo rechaza o no hay red; nunca lanza."""
    propio = client is None
    http = client or httpx.Client(timeout=10.0)
    try:
        resp = http.post(
            f"{_TELEGRAM_API}/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text},
        )
        return resp.status_code == 200
    except httpx.HTTPError:
        return False
    finally:
        if propio:
            http.close()


def daily_summary(ultimas: Last24h, *, pending_total: int, brake: str) -> str:
    """El parte del día: la última jornada primero, el histórico aparte.

    El operador publica en redes con lo de las últimas 24 h; el total de la cola
    incluye histórico viejo y no dice si esa jornada está lista.
    """
    resueltos = ultimas.curated + ultimas.discarded
    if ultimas.pending == 0:
        jornada = "Última jornada lista para publicar: todo está resuelto."
    else:
        jornada = (
            f"Te quedan {ultimas.pending} sin procesar de la última jornada "
            f"(el más viejo espera {ultimas.oldest_pending_hours} h)."
        )
    return (
        f"Últimas 24 h: entraron {ultimas.entered}, resueltos {resueltos} "
        f"({ultimas.curated} aceptados, {ultimas.discarded} descartados).\n"
        f"{jornada}\n"
        f"Cola total (con histórico): {pending_total} · carril 1: {brake}"
    )


def collect(session, settings: Settings) -> tuple[list[Alert], str]:
    """Lo que está mal ahora y una línea de resumen para el parte diario."""
    snapshot = load_strip(session, settings)
    alertas: list[Alert] = []
    for ind in snapshot.strip:
        if ind.level in _RANK:
            hint = f" → {ind.hint}" if ind.hint else ""
            alertas.append(Alert(ind.key, ind.level, f"{ind.label}: {ind.value}.{hint}"))
    freno_txt = "apagado"
    if settings.lane1_enabled:
        freno = lane1.brake(session, settings)
        if freno.on:
            freno_txt = "FRENADO"
            alertas.append(Alert("lane1_brake", "down", f"Carril 1 frenado: {freno.reason}"))
        else:
            freno_txt = "andando"
    resumen = daily_summary(
        last_24h(session), pending_total=snapshot.intake.pending, brake=freno_txt
    )
    return alertas, resumen


def _read_state(path: Path) -> dict[str, dict[str, str]]:
    try:
        datos: dict[str, dict[str, str]] = json.loads(path.read_text())
        return datos
    except (OSError, ValueError):
        return {}


def _write_state(path: Path, state: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Avisos al celular por Telegram. En seco por defecto.")
    parser.add_argument("--execute", action="store_true", help="manda de verdad y guarda el estado")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    settings = get_settings()
    ahora = datetime.now(UTC)
    with session_scope() as session:
        alertas, resumen = collect(session, settings)

    estado = _read_state(settings.alerts_state_path)
    mensajes, nuevo = decide(alertas, estado, now=ahora)
    hoy = ahora.date().isoformat()
    if ahora.hour >= HEARTBEAT_HOUR_UTC and estado.get(_HEARTBEAT_KEY, {}).get("day") != hoy:
        cabeza = "Parte diario de SAIMC"
        problemas = f"\n{len(alertas)} aviso(s) abierto(s)." if alertas else "\nTodo en orden."
        mensajes.append(f"📋 {cabeza}\n{resumen}{problemas}")
        nuevo[_HEARTBEAT_KEY] = {"day": hoy}

    if not mensajes:
        print("Sin avisos nuevos.")
        sys.exit(0)
    for m in mensajes:
        print(m, end="\n\n")
    if not args.execute:
        print("En seco: no se mandó nada. Para mandar, --execute.")
        sys.exit(0)
    token, chat = settings.telegram_bot_token, settings.telegram_chat_id
    if not token or not chat:
        print("Falta SAIMC_TELEGRAM_BOT_TOKEN o SAIMC_TELEGRAM_CHAT_ID: no se mandó nada.")
        sys.exit(2)
    if all(send_telegram(token, chat, m) for m in mensajes):
        _write_state(settings.alerts_state_path, nuevo)
        sys.exit(0)
    # Sin guardar el estado, la próxima corrida lo reintenta.
    print("Telegram no aceptó el mensaje; se reintenta en la próxima corrida.")
    sys.exit(1)


if __name__ == "__main__":
    main()
