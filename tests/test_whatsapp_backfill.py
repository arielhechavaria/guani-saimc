"""Tests del backfill de histórico de WAHA — casos armados con la forma real.

`parse_history` se prueba con la forma exacta que devolvió la instancia real
del operador contra `.../messages/preview` (Groupej Morales Surl, verificado
2026-08-17 — no es la forma que documenta WAHA, que muestra el objeto de
mensaje sin envoltorio). `parse_group_history` se prueba con la forma que
devolvió la misma instancia contra `/chats/{id}/messages` (verificado
2026-08-18): objeto de mensaje plano, SIN envoltorio — son dos endpoints
distintos con dos formas distintas. El transporte (`fetch_channel_history`)
se prueba con `httpx.MockTransport`, nunca con un socket real — mismo
criterio que `tests/test_exchange_rate.py`.
"""

from datetime import UTC, datetime

import httpx

from saimc.ingestion.whatsapp_backfill import (
    fetch_channel_history,
    parse_group_history,
    parse_history,
)

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


# --- parse_group_history ------------------------------------------------------


class TestParseGroupHistory:
    """Forma real de `/chats/{id}/messages`, medida contra la instancia del
    operador el 2026-08-18: el objeto de mensaje viene PLANO en la lista, sin
    el envoltorio `{"message", "reactions"}` del endpoint de canales."""

    def test_entrada_plana_con_texto_se_parsea(self) -> None:
        entradas = [
            {
                "from": "120363406372093216@g.us",
                "fromMe": False,
                "body": "Aceite girasol 1L a 1200",
                "timestamp": 1787064971,
                "hasMedia": False,
                "participant": "225318866522137@lid",
            }
        ]

        resultado = parse_group_history(entradas)

        assert len(resultado) == 1
        assert resultado[0].text == "Aceite girasol 1L a 1200"
        # El `from` de un grupo es el JID del GRUPO, no el del emisor: es lo
        # que el webhook matchea contra `Channel.external_id`.
        assert resultado[0].chat_id == "120363406372093216@g.us"
        assert resultado[0].timestamp == datetime.fromtimestamp(1787064971, tz=UTC)

    def test_entrada_de_solo_imagen_sin_caption_se_descarta(self) -> None:
        """Mayoritario en varios grupos medidos — «MIPYME LA GRANDEZA» tenía
        183 de 200 mensajes en imagen sin texto."""
        entradas = [
            {
                "from": "1@g.us",
                "fromMe": False,
                "body": "",
                "timestamp": 1787064971,
                "hasMedia": True,
            }
        ]

        assert parse_group_history(entradas) == []

    def test_mensaje_propio_se_descarta(self) -> None:
        """En grupos pesa mucho más que en canales: «Mercado Santiago 2.3»
        tenía 100 de 200 mensajes del propio operador (medido 2026-08-18)."""
        entradas = [
            {
                "from": "1@g.us",
                "fromMe": True,
                "body": "gracias, lo llevo",
                "timestamp": 1787064971,
            }
        ]

        assert parse_group_history(entradas) == []

    def test_entrada_envuelta_de_canal_no_se_parsea_como_grupo(self) -> None:
        """Guarda del ruteo: si por error se le pasa la forma del endpoint de
        canales, no hay `from` en el nivel de arriba y la entrada se descarta
        en vez de colarse a medio parsear."""
        entradas = [
            {
                "message": {
                    "from": "1@newsletter",
                    "fromMe": False,
                    "body": "Arroz a 30885",
                    "timestamp": 1786719527,
                },
                "reactions": {},
            }
        ]

        assert parse_group_history(entradas) == []

    def test_entrada_sin_forma_reconocible_se_descarta_sin_romper_el_lote(self) -> None:
        entradas = [
            "esto no es un dict",
            {"body": "sin from"},
            {
                "from": "1@g.us",
                "fromMe": False,
                "body": "único mensaje válido",
                "timestamp": 1787064971,
            },
        ]

        resultado = parse_group_history(entradas)

        assert len(resultado) == 1
        assert resultado[0].text == "único mensaje válido"

    def test_lista_vacia(self) -> None:
        assert parse_group_history([]) == []


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
        # El límite es fijo (medido, no configurable) y se afirma como literal
        # a propósito: subirlo tiene que ser una decisión, no un descuido. El
        # valor y por qué ese, en el comentario de `_HISTORY_LIMIT`.
        assert capturado["limit"] == "250"

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


# --- ruteo canal vs grupo -----------------------------------------------------


class TestRuteoPorTipoDeJid:
    """El sufijo del JID decide endpoint y parser (medido 2026-08-18): WhatsApp
    ya declara el tipo, así que no hay nada que el operador pueda tipear mal."""

    def test_jid_de_grupo_usa_el_endpoint_de_chats_y_la_forma_plana(self) -> None:
        capturado: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            capturado["url"] = str(request.url)
            return httpx.Response(
                200,
                json=[
                    {
                        "from": "120363406372093216@g.us",
                        "fromMe": False,
                        "body": "Pollo a 500",
                        "timestamp": 1787064971,
                        "participant": "225318866522137@lid",
                    }
                ],
            )

        resultado = fetch_channel_history(
            "http://127.0.0.1:3000",
            "clave",
            "default",
            "120363406372093216@g.us",
            timeout=5.0,
            transport=httpx.MockTransport(handler),
        )

        assert resultado is not None
        assert len(resultado) == 1
        assert resultado[0].text == "Pollo a 500"
        assert "/api/default/chats/120363406372093216@g.us/messages" in str(capturado["url"])
        assert "/channels/" not in str(capturado["url"])

    def test_jid_de_canal_sigue_usando_el_endpoint_de_canales(self) -> None:
        """Regresión: el ruteo nuevo no debe mover de lugar a los canales, que
        ya funcionan en producción."""
        capturado: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            capturado["url"] = str(request.url)
            return httpx.Response(
                200,
                json=[
                    {
                        "message": {
                            "from": "1@newsletter",
                            "fromMe": False,
                            "body": "Arroz a 30885",
                            "timestamp": 1786719527,
                        },
                        "reactions": {},
                    }
                ],
            )

        resultado = fetch_channel_history(
            "http://127.0.0.1:3000",
            "clave",
            "default",
            "1@newsletter",
            timeout=5.0,
            transport=httpx.MockTransport(handler),
        )

        assert resultado is not None
        assert len(resultado) == 1
        assert resultado[0].text == "Arroz a 30885"
        assert "/api/default/channels/1@newsletter/messages/preview" in str(capturado["url"])

    def test_jid_de_grupo_con_sufijo_lid_tambien_usa_el_endpoint_de_chats(self) -> None:
        """Regresión (docs/decisions.md, 2026-08-21): un grupo real capturado
        con `external_id` terminado en `@lid` —el identificador de privacidad
        que WhatsApp usa cada vez más en vez de `@g.us`— caía al branch de
        canal por descarte y WAHA respondía 500. Medido contra la instancia
        real: el mismo endpoint de chats que ya sirve `@g.us` sirve `@lid`."""
        capturado: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            capturado["url"] = str(request.url)
            return httpx.Response(
                200,
                json=[
                    {
                        "from": "157724134363163@lid",
                        "fromMe": False,
                        "body": "Arroz a 340",
                        "timestamp": 1787335937,
                    }
                ],
            )

        resultado = fetch_channel_history(
            "http://127.0.0.1:3000",
            "clave",
            "default",
            "157724134363163@lid",
            timeout=5.0,
            transport=httpx.MockTransport(handler),
        )

        assert resultado is not None
        assert len(resultado) == 1
        assert resultado[0].text == "Arroz a 340"
        assert "/api/default/chats/157724134363163@lid/messages" in str(capturado["url"])
        assert "/channels/" not in str(capturado["url"])

    def test_grupo_con_sesion_caida_devuelve_none(self) -> None:
        """Misma política de error que el endpoint de canales: 200 con un dict
        de error en vez de una lista no es histórico vacío, es fallo."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"error": "Session status is not as expected.", "session": "default"},
            )

        resultado = fetch_channel_history(
            "http://127.0.0.1:3000",
            "clave",
            "default",
            "1@g.us",
            timeout=5.0,
            transport=httpx.MockTransport(handler),
        )

        assert resultado is None


class TestBackfillIgnoraFotos:
    """El backfill sigue siendo de TEXTO, aunque `parse_message` ya acepte fotos.

    Desde el 2026-08-21 `parse_message` devuelve un mensaje con `text` vacío
    cuando hay una foto descargable, para que el webhook la ingiera. El
    backfill NO comparte ese camino: escribe `parsed.text` a un `.txt` y lo
    ingiere, así que un mensaje sin texto le crearía un archivo vacío.

    Traer también las fotos del histórico es su propio slice: `fetch_channel_history`
    pide `downloadMedia=false`, y el techo de `_HISTORY_LIMIT = 250` se midió
    con esa bandera apagada — encenderla cambia el costo por completo y puede
    colgar la sesión de WAHA.
    """

    def test_una_foto_del_historico_no_entra_como_mensaje_vacio(self) -> None:
        entradas = [
            {
                "from": "1@g.us",
                "body": "",
                "timestamp": 1,
                "hasMedia": True,
                "media": {
                    "url": "http://localhost:3000/api/files/foto.jpg",
                    "mimetype": "image/jpeg",
                },
            }
        ]

        assert parse_group_history(entradas) == []

    def test_el_texto_del_historico_sigue_entrando(self) -> None:
        entradas = [{"from": "1@g.us", "body": "aceite a 950", "timestamp": 1}]

        (mensaje,) = parse_group_history(entradas)
        assert mensaje.text == "aceite a 950"

    def test_lo_mismo_para_el_endpoint_de_canales(self) -> None:
        entradas = [
            {
                "message": {
                    "from": "1@newsletter",
                    "body": "",
                    "timestamp": 1,
                    "hasMedia": True,
                    "media": {
                        "url": "http://localhost:3000/api/files/foto.jpg",
                        "mimetype": "image/jpeg",
                    },
                }
            }
        ]

        assert parse_history(entradas) == []
