"""Tests del backfill de histórico de WAHA — casos armados con la forma real.

`parse_history` se prueba con la forma exacta que devolvió la instancia real
del operador contra `.../messages/preview` (Groupej Morales Surl, verificado
2026-08-17 — no es la forma que documenta WAHA, que muestra el objeto de
mensaje sin envoltorio). El transporte (`fetch_channel_history`) se prueba
con `httpx.MockTransport`, nunca con un socket real — mismo criterio que
`tests/test_exchange_rate.py`.
"""

from datetime import UTC, datetime

import httpx

from saimc.ingestion.whatsapp_backfill import fetch_channel_history, parse_history

# --- parse_history ------------------------------------------------------------


class TestParseHistory:
    def test_entrada_envuelta_con_texto_se_parsea(self) -> None:
        """Forma real de .../messages/preview: el mensaje va anidado bajo
        "message", junto a "reactions" — distinto del webhook, que recibe el
        objeto directo."""
        entradas = [
            {
                "message": {
                    "from": "120363419630368180@newsletter",
                    "fromMe": False,
                    "body": "Arroz grano dorado 100 lb  43.5 o 30885",
                    "timestamp": 1786719527,
                    "hasMedia": False,
                },
                "reactions": {},
            }
        ]

        resultado = parse_history(entradas)

        assert len(resultado) == 1
        assert resultado[0].text == "Arroz grano dorado 100 lb  43.5 o 30885"
        assert resultado[0].chat_id == "120363419630368180@newsletter"
        assert resultado[0].timestamp == datetime.fromtimestamp(1786719527, tz=UTC)

    def test_entrada_de_solo_imagen_sin_caption_se_descarta(self) -> None:
        """Caso real mayoritario del corpus medido (66/96 mensajes): imagen
        sin texto. Sin OCR desde acá (mismo criterio que el webhook), no hay
        nada que ingerir."""
        entradas = [
            {
                "message": {
                    "from": "1@newsletter",
                    "fromMe": False,
                    "body": "",
                    "timestamp": 1786719527,
                    "hasMedia": True,
                },
                "reactions": {},
            }
        ]

        assert parse_history(entradas) == []

    def test_mensaje_propio_se_descarta(self) -> None:
        entradas = [
            {
                "message": {
                    "from": "1@newsletter",
                    "fromMe": True,
                    "body": "respuesta del operador",
                    "timestamp": 1786719527,
                },
                "reactions": {},
            }
        ]

        assert parse_history(entradas) == []

    def test_entrada_sin_forma_reconocible_se_descarta_sin_romper_el_lote(self) -> None:
        entradas = [
            "esto no es un dict",
            {"reactions": {}},  # sin clave "message"
            {
                "message": {
                    "from": "1@newsletter",
                    "fromMe": False,
                    "body": "único mensaje válido",
                    "timestamp": 1786719527,
                },
                "reactions": {},
            },
        ]

        resultado = parse_history(entradas)

        assert len(resultado) == 1
        assert resultado[0].text == "único mensaje válido"

    def test_lista_vacia(self) -> None:
        assert parse_history([]) == []


# --- fetch_channel_history ----------------------------------------------------


class TestFetchChannelHistory:
    def test_consulta_exitosa_devuelve_mensajes_parseados(self) -> None:
        capturado: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            capturado["url"] = str(request.url)
            capturado["api_key"] = request.headers.get("x-api-key")
            capturado["limit"] = request.url.params.get("limit")
            return httpx.Response(
                200,
                json=[
                    {
                        "message": {
                            "from": "1@newsletter",
                            "fromMe": False,
                            "body": "Pollo a 500",
                            "timestamp": 1786719527,
                        },
                        "reactions": {},
                    }
                ],
            )

        transport = httpx.MockTransport(handler)

        resultado = fetch_channel_history(
            "http://127.0.0.1:3000",
            "clave-de-prueba",
            "default",
            "1@newsletter",
            timeout=5.0,
            transport=transport,
        )

        assert resultado is not None
        assert len(resultado) == 1
        assert resultado[0].text == "Pollo a 500"
        assert "/api/default/channels/1@newsletter/messages/preview" in str(capturado["url"])
        assert capturado["api_key"] == "clave-de-prueba"
        # El límite es fijo (medido, no configurable) — ver docstring del módulo.
        assert capturado["limit"] == "100"

    def test_respuesta_no_200_devuelve_none(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"statusCode": 500, "exception": {}})

        resultado = fetch_channel_history(
            "http://127.0.0.1:3000",
            "clave",
            "default",
            "1@newsletter",
            timeout=5.0,
            transport=httpx.MockTransport(handler),
        )

        assert resultado is None

    def test_sesion_de_waha_no_lista_devuelve_none(self) -> None:
        """Forma real vista contra la instancia del operador cuando la sesión
        de WAHA no está lista: 200 con un dict de error, no una lista."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "error": "Session status is not as expected. Try again later.",
                    "session": "default",
                },
            )

        resultado = fetch_channel_history(
            "http://127.0.0.1:3000",
            "clave",
            "default",
            "1@newsletter",
            timeout=5.0,
            transport=httpx.MockTransport(handler),
        )

        assert resultado is None

    def test_error_de_red_devuelve_none_sin_propagar(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no se pudo conectar", request=request)

        resultado = fetch_channel_history(
            "http://127.0.0.1:3000",
            "clave",
            "default",
            "1@newsletter",
            timeout=5.0,
            transport=httpx.MockTransport(handler),
        )

        assert resultado is None

    def test_cuerpo_no_json_devuelve_none(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>no es json</html>")

        resultado = fetch_channel_history(
            "http://127.0.0.1:3000",
            "clave",
            "default",
            "1@newsletter",
            timeout=5.0,
            transport=httpx.MockTransport(handler),
        )

        assert resultado is None
