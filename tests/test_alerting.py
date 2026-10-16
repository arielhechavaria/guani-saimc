"""Avisos al celular: cuándo se manda un mensaje y cuándo no.

La lógica de decisión es pura (`decide`): recibe lo que está mal ahora y lo
que ya se avisó, y devuelve qué mandar. Los casos se definieron a mano antes
del código.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from saimc.alerting import Alert, decide, send_telegram

T0 = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def _alert(key: str = "waha", level: str = "down", text: str = "WAHA caído") -> Alert:
    return Alert(key=key, level=level, text=text)


def test_problema_nuevo_se_avisa_una_vez() -> None:
    mensajes, estado = decide([_alert()], {}, now=T0)
    assert len(mensajes) == 1
    assert "WAHA caído" in mensajes[0]
    assert "waha" in estado


def test_mismo_problema_no_se_repite_antes_del_recordatorio() -> None:
    _, estado = decide([_alert()], {}, now=T0)
    mensajes, _ = decide([_alert()], estado, now=T0 + timedelta(hours=1))
    assert mensajes == []


def test_mismo_problema_se_recuerda_a_las_24_horas() -> None:
    _, estado = decide([_alert()], {}, now=T0)
    mensajes, _ = decide([_alert()], estado, now=T0 + timedelta(hours=24))
    assert len(mensajes) == 1


def test_problema_resuelto_avisa_que_se_recupero_y_limpia_el_estado() -> None:
    _, estado = decide([_alert()], {}, now=T0)
    mensajes, nuevo = decide([], estado, now=T0 + timedelta(hours=1))
    assert len(mensajes) == 1
    assert "WAHA caído" in mensajes[0]
    assert "waha" not in nuevo


def test_empeorar_de_warn_a_down_avisa_de_nuevo() -> None:
    _, estado = decide([_alert(level="warn")], {}, now=T0)
    mensajes, _ = decide([_alert(level="down")], estado, now=T0 + timedelta(minutes=15))
    assert len(mensajes) == 1


def test_mejorar_de_down_a_warn_no_manda_nada() -> None:
    _, estado = decide([_alert(level="down")], {}, now=T0)
    mensajes, _ = decide([_alert(level="warn")], estado, now=T0 + timedelta(minutes=15))
    assert mensajes == []


def test_varios_problemas_nuevos_van_en_un_solo_mensaje() -> None:
    mensajes, _ = decide([_alert("waha"), _alert("prefill", "warn", "Pre-cálculo viejo")], {}, now=T0)
    assert len(mensajes) == 1
    assert "WAHA caído" in mensajes[0]
    assert "Pre-cálculo viejo" in mensajes[0]


def test_send_telegram_postea_al_bot_con_chat_y_texto() -> None:
    vistos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistos.append(request)
        return httpx.Response(200, json={"ok": True})

    cliente = httpx.Client(transport=httpx.MockTransport(handler))
    assert send_telegram("123:ABC", "42", "hola", client=cliente) is True
    assert vistos[0].url.path == "/bot123:ABC/sendMessage"
    assert b"chat_id=42" in vistos[0].content
    assert b"text=hola" in vistos[0].content


def test_send_telegram_devuelve_false_si_el_bot_rechaza() -> None:
    cliente = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    assert send_telegram("mal", "42", "hola", client=cliente) is False


def test_send_telegram_devuelve_false_si_no_hay_red() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin red")

    cliente = httpx.Client(transport=httpx.MockTransport(handler))
    assert send_telegram("123:ABC", "42", "hola", client=cliente) is False


# --- el parte diario con las últimas 24 h --------------------------------------------


def test_el_parte_dice_cuanto_entro_cuanto_se_resolvio_y_cuanto_falta() -> None:
    from saimc.alerting import daily_summary
    from saimc.progress import Last24h

    ultimas = Last24h(entered=133, curated=13, discarded=95, pending=25, oldest_pending_hours=9)

    texto = daily_summary(ultimas, pending_total=600, brake="andando")

    assert "133" in texto and "entraron" in texto
    assert "108" in texto  # resueltos: 13 + 95
    assert "25" in texto and "9 h" in texto
    assert "600" in texto  # el histórico, aparte
    assert "andando" in texto


def test_el_parte_sin_pendientes_en_las_24_horas_dice_que_esta_lista() -> None:
    from saimc.alerting import daily_summary
    from saimc.progress import Last24h

    ultimas = Last24h(entered=40, curated=10, discarded=30, pending=0, oldest_pending_hours=None)

    texto = daily_summary(ultimas, pending_total=500, brake="andando")

    assert "lista para publicar" in texto
