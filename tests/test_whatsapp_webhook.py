"""Webhook de WAHA — canales de WhatsApp automatizados (docs/decisions.md, 2026-08-15).

Apartamiento consciente de la decisión cerrada 2 (nada de scraping/bots en
Fase 0) y de la decisión cerrada 4 (nada de Docker en Fase 0): el operador
corre WAHA en Docker en su propio VPS y este webhook es el receptor. Se anotó
en `docs/decisions.md` con el motivo.

`verify_hmac` y `parse_message` son funciones puras, testeadas contra la forma
de payload documentada por WAHA (waha.devlike.pro/docs/how-to/events/,
verificado 2026-08-15) — no contra una instancia real, que todavía no existe.
El caso esperado se define acá, independientemente de correr WAHA.
"""

import asyncio
import hashlib
import hmac
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.ingestion.whatsapp_media import fetch_media, is_trusted_media_url
from saimc.ingestion.whatsapp_webhook import is_foreign_session, parse_message, verify_hmac
from saimc.models import Channel, Platform, ProcessingStatus, RawIngestion, SourceType
from saimc.web.app import app
from saimc.web.deps import get_db

# --- verify_hmac --------------------------------------------------------------


class TestVerifyHmac:
    def test_firma_valida_pasa(self) -> None:
        secret = b"clave-del-operador"
        body = b'{"from":"1@newsletter","body":"hola"}'
        firma = hmac.new(secret, body, hashlib.sha512).hexdigest()

        assert verify_hmac(secret, body, firma) is True

    def test_firma_de_otra_clave_no_pasa(self) -> None:
        body = b'{"from":"1@newsletter","body":"hola"}'
        firma_ajena = hmac.new(b"otra-clave", body, hashlib.sha512).hexdigest()

        assert verify_hmac(b"clave-del-operador", body, firma_ajena) is False

    def test_body_alterado_no_pasa(self) -> None:
        secret = b"clave-del-operador"
        firma = hmac.new(secret, b"original", hashlib.sha512).hexdigest()

        assert verify_hmac(secret, b"alterado", firma) is False

    def test_firma_vacia_no_pasa(self) -> None:
        assert verify_hmac(b"clave", b"cualquier cosa", "") is False


# --- parse_message --------------------------------------------------------------


class TestParseMessage:
    def test_payload_plano_documentado_por_waha(self) -> None:
        """Forma exacta que muestra la documentación de WAHA para el evento
        `message` (waha.devlike.pro/docs/how-to/events/, 2026-08-15)."""
        payload = {
            "id": "true_11111111111@c.us_AAAA",
            "timestamp": 1667561485,
            "from": "11111111111@newsletter",
            "fromMe": False,
            "body": "Hi there!",
            "hasMedia": False,
        }

        mensaje = parse_message(payload)

        assert mensaje is not None
        assert mensaje.chat_id == "11111111111@newsletter"
        assert mensaje.text == "Hi there!"
        assert mensaje.timestamp == datetime(2022, 11, 4, 11, 31, 25, tzinfo=UTC)
        assert mensaje.has_media is False

    def test_payload_envuelto_en_event_y_payload(self) -> None:
        """La convención general de WAHA envuelve cada evento en
        `{"event": ..., "session": ..., "payload": {...}}`. El parser tiene
        que aceptar las dos formas: no hay certeza de cuál llega hasta probar
        contra la instancia real del operador."""
        envelope = {
            "event": "message",
            "session": "default",
            "payload": {
                "id": "true_1@newsletter_BBBB",
                "timestamp": 1667561485,
                "from": "1@newsletter",
                "body": "aceite a 950",
                "hasMedia": False,
            },
        }

        mensaje = parse_message(envelope)

        assert mensaje is not None
        assert mensaje.chat_id == "1@newsletter"
        assert mensaje.text == "aceite a 950"

    def test_mensaje_propio_se_descarta(self) -> None:
        """`fromMe: true` es un mensaje que el propio número dedicado mandó
        (confirmaciones, respuestas), no un anuncio del canal."""
        payload = {"from": "1@newsletter", "body": "ok", "fromMe": True, "timestamp": 1}

        assert parse_message(payload) is None

    def test_sin_texto_y_sin_url_de_media_se_descarta(self) -> None:
        """`hasMedia: true` sin `media.url` significa que WAHA detectó el
        archivo y NO lo descargó (su `WHATSAPP_DOWNLOAD_MEDIA` o su
        `WHATSAPP_FILES_MIMETYPES` lo filtraron). No hay nada que bajar, así
        que no se crea una fila vacía."""
        payload = {"from": "1@newsletter", "hasMedia": True, "body": "", "timestamp": 1}

        assert parse_message(payload) is None

    def test_foto_sin_caption_con_url_descargable_entra(self) -> None:
        """El caso que este slice existe para arreglar: 183 de 200 mensajes de
        un grupo que publica solo fotos (medido el 2026-08-18)."""
        payload = {
            "from": "1@newsletter",
            "hasMedia": True,
            "body": "",
            "timestamp": 1,
            "media": {
                "url": "http://localhost:3000/api/files/foto.jpg",
                "mimetype": "image/jpeg",
            },
        }

        mensaje = parse_message(payload)

        assert mensaje is not None
        assert mensaje.text == ""
        assert mensaje.media_url == "http://localhost:3000/api/files/foto.jpg"
        assert mensaje.media_suffix == ".jpg"

    def test_media_que_no_es_imagen_se_descarta(self) -> None:
        """Un video o un audio no es un anuncio legible: no hay OCR que
        aplicarle y ocuparía la cola sin aportar un precio."""
        payload = {
            "from": "1@newsletter",
            "hasMedia": True,
            "body": "",
            "timestamp": 1,
            "media": {
                "url": "http://localhost:3000/api/files/clip.mp4",
                "mimetype": "video/mp4",
            },
        }

        assert parse_message(payload) is None

    def test_sticker_webp_se_descarta(self) -> None:
        """WhatsApp reencoda las fotos a JPEG; lo que llega como `image/webp`
        es casi siempre un sticker. Dejarlo entrar inundaría la cola de
        caritas. Se pierde la foto webp rara: costo de recall aceptado a
        cambio de la precisión (docs/decisions.md, 2026-08-21)."""
        payload = {
            "from": "1@newsletter",
            "hasMedia": True,
            "body": "",
            "timestamp": 1,
            "media": {
                "url": "http://localhost:3000/api/files/sticker.webp",
                "mimetype": "image/webp",
            },
        }

        assert parse_message(payload) is None

    def test_foto_con_caption_sigue_entrando_como_texto(self) -> None:
        """Comportamiento previo intacto: cuando hay caption, el texto es lo
        que se ingiere. Preservar TAMBIEN la foto como evidencia es un slice
        aparte — exige que la cola sepa proponer desde un `original_text` de
        una fila `image`, que hoy no hace."""
        payload = {
            "from": "1@newsletter",
            "hasMedia": True,
            "body": "aceite a 950",
            "timestamp": 1,
            "media": {
                "url": "http://localhost:3000/api/files/foto.jpg",
                "mimetype": "image/jpeg",
            },
        }

        mensaje = parse_message(payload)

        assert mensaje is not None
        assert mensaje.text == "aceite a 950"

    def test_sin_chat_id_se_descarta(self) -> None:
        assert parse_message({"body": "aceite a 950", "timestamp": 1}) is None

    def test_payload_no_es_diccionario_se_descarta(self) -> None:
        assert parse_message("no soy un json valido") is None  # type: ignore[arg-type]


# --- is_foreign_session ----------------------------------------------------------


class TestIsForeignSession:
    def test_otra_sesion_es_ajena(self) -> None:
        assert is_foreign_session({"session": "guani-canal", "payload": {}}, "default") is True

    def test_la_sesion_de_ingesta_no_es_ajena(self) -> None:
        assert is_foreign_session({"session": "default", "payload": {}}, "default") is False

    def test_sin_campo_session_no_se_puede_decir_y_no_es_ajena(self) -> None:
        """La forma plana (sin envoltorio) no trae sesión: se conserva el
        comportamiento de siempre en vez de descartar a ciegas."""
        assert is_foreign_session({"from": "1@newsletter", "body": "x"}, "default") is False

    def test_un_cuerpo_que_no_es_diccionario_no_es_ajeno(self) -> None:
        assert is_foreign_session(["x"], "default") is False


# --- endpoint --------------------------------------------------------------


def _firmar(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()


@pytest.fixture
def client(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    monkeypatch.setenv("SAIMC_INBOX_DIR", str(tmp_path / "inbox"))
    monkeypatch.setenv("SAIMC_RAW_STORE_DIR", str(tmp_path / "data" / "raw"))
    monkeypatch.setenv("SAIMC_WHATSAPP_WEBHOOK_HMAC_KEY", "clave-de-prueba")
    # La descarga de fotos sin caption (2026-08-21) pide la URL a la misma
    # instancia de WAHA, con su `X-Api-Key`: sin estos dos, `fetch_media` no
    # se llama nunca y la foto se descarta.
    monkeypatch.setenv("SAIMC_WAHA_BASE_URL", "http://localhost:3000")
    monkeypatch.setenv("SAIMC_WAHA_API_KEY", "clave-waha")
    # La sesión de ingesta, fija: el guard de sesión ajena (2026-10-20) la
    # compara contra el `session` del evento, y un `.env` con otra la cambiaría.
    monkeypatch.setenv("SAIMC_WAHA_SESSION", "default")
    get_settings.cache_clear()

    app.dependency_overrides[get_db] = _get_db_override
    yield TestClient(app)
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _canal(session: Session, *, external_id: str = "1@newsletter", is_active: bool = True) -> Channel:
    canal = Channel(
        name="Tienda: The Boss",
        platform=Platform.WHATSAPP_CHANNEL,
        external_id=external_id,
        is_active=is_active,
    )
    session.add(canal)
    session.commit()
    return canal


def _ingestas(session: Session) -> list[RawIngestion]:
    return list(session.execute(select(RawIngestion)).scalars())


class TestWebhookEndpoint:
    def test_mensaje_valido_de_canal_registrado_entra_pendiente(
        self, client: TestClient, session: Session
    ) -> None:
        _canal(session)
        cuerpo = json.dumps(
            {"from": "1@newsletter", "body": "aceite 1L a 950", "timestamp": 1755000000}
        ).encode()

        response = client.post(
            "/webhooks/whatsapp",
            content=cuerpo,
            headers={
                "content-type": "application/json",
                "X-Webhook-Hmac": _firmar("clave-de-prueba", cuerpo),
            },
        )

        assert response.status_code == 200
        ingestas = _ingestas(session)
        assert len(ingestas) == 1
        assert ingestas[0].source_type == SourceType.WHATSAPP_CHANNEL
        assert ingestas[0].source_reference == "Tienda: The Boss"
        assert ingestas[0].processing_status == ProcessingStatus.PENDING
        assert "aceite 1L a 950" in (ingestas[0].original_text or "")

    def test_mensaje_de_canal_registrado_guarda_channel_id(
        self, client: TestClient, session: Session
    ) -> None:
        """Identidad real de origen (docs/decisions.md, 2026-08-19): el
        webhook ya resolvió el canal por `external_id`, no hace falta
        reconstruirlo por nombre en curaduría."""
        canal = _canal(session)
        cuerpo = json.dumps(
            {"from": "1@newsletter", "body": "aceite 1L a 950", "timestamp": 1755000000}
        ).encode()

        client.post(
            "/webhooks/whatsapp",
            content=cuerpo,
            headers={
                "content-type": "application/json",
                "X-Webhook-Hmac": _firmar("clave-de-prueba", cuerpo),
            },
        )

        (ingesta,) = _ingestas(session)
        assert ingesta.channel_id == canal.id

    def test_sin_firma_hmac_rechaza(self, client: TestClient, session: Session) -> None:
        _canal(session)
        cuerpo = json.dumps({"from": "1@newsletter", "body": "x", "timestamp": 1}).encode()

        response = client.post(
            "/webhooks/whatsapp", content=cuerpo, headers={"content-type": "application/json"}
        )

        assert response.status_code == 401
        assert _ingestas(session) == []

    def test_firma_hmac_incorrecta_rechaza(self, client: TestClient, session: Session) -> None:
        _canal(session)
        cuerpo = json.dumps({"from": "1@newsletter", "body": "x", "timestamp": 1}).encode()

        response = client.post(
            "/webhooks/whatsapp",
            content=cuerpo,
            headers={"content-type": "application/json", "X-Webhook-Hmac": "no-es-la-firma"},
        )

        assert response.status_code == 401
        assert _ingestas(session) == []

    def test_sin_clave_configurada_rechaza_todo(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mismo criterio que `require_operator`: sin secreto configurado, el
        webhook cierra en falso en vez de aceptar sin verificar nada.

        `setenv("", ...)`, no `delenv`: pydantic-settings lee `.env` del
        disco como fuente propia — si ese archivo real ya tiene la clave
        (como en este repo, para producción), borrar la variable de entorno
        no alcanza para simular "sin configurar", hay que pisarla con vacío.
        """
        monkeypatch.setenv("SAIMC_WHATSAPP_WEBHOOK_HMAC_KEY", "")
        get_settings.cache_clear()
        _canal(session)
        cuerpo = json.dumps({"from": "1@newsletter", "body": "x", "timestamp": 1}).encode()

        response = client.post(
            "/webhooks/whatsapp",
            content=cuerpo,
            headers={"content-type": "application/json", "X-Webhook-Hmac": "lo-que-sea"},
        )

        assert response.status_code == 503
        assert _ingestas(session) == []

    def test_canal_no_registrado_se_ignora_sin_romper(
        self, client: TestClient, session: Session
    ) -> None:
        """Ningún canal con ese `external_id` en la base: WAHA no debe recibir
        un error que lo empuje a reintentar indefinidamente."""
        cuerpo = json.dumps(
            {"from": "999@newsletter", "body": "x", "timestamp": 1}
        ).encode()

        response = client.post(
            "/webhooks/whatsapp",
            content=cuerpo,
            headers={
                "content-type": "application/json",
                "X-Webhook-Hmac": _firmar("clave-de-prueba", cuerpo),
            },
        )

        assert response.status_code == 200
        assert _ingestas(session) == []

    def test_canal_apagado_se_ignora(self, client: TestClient, session: Session) -> None:
        _canal(session, is_active=False)
        cuerpo = json.dumps({"from": "1@newsletter", "body": "x", "timestamp": 1}).encode()

        response = client.post(
            "/webhooks/whatsapp",
            content=cuerpo,
            headers={
                "content-type": "application/json",
                "X-Webhook-Hmac": _firmar("clave-de-prueba", cuerpo),
            },
        )

        assert response.status_code == 200
        assert _ingestas(session) == []

    def _post_envuelto(self, client: TestClient, sesion: str) -> httpx.Response:
        cuerpo = json.dumps(
            {
                "event": "message.any",
                "session": sesion,
                "payload": {"from": "1@newsletter", "body": "aceite 1L a 950", "timestamp": 1},
            }
        ).encode()
        return client.post(
            "/webhooks/whatsapp",
            content=cuerpo,
            headers={
                "content-type": "application/json",
                "X-Webhook-Hmac": _firmar("clave-de-prueba", cuerpo),
            },
        )

    def test_evento_de_otra_sesion_de_waha_no_se_ingiere(
        self, client: TestClient, session: Session
    ) -> None:
        """El webhook de WAHA es global por contenedor: la sesión que publica
        en el canal de Guani (`guani-canal`, docs/decisions.md 2026-10-20)
        también manda sus eventos acá. Aunque el chat sea un canal registrado,
        no es la sesión de ingesta y no entra."""
        _canal(session)

        response = self._post_envuelto(client, "guani-canal")

        assert response.status_code == 200
        assert response.json()["ingested"] == 0
        assert _ingestas(session) == []

    def test_evento_de_la_sesion_de_ingesta_entra(
        self, client: TestClient, session: Session
    ) -> None:
        _canal(session)

        response = self._post_envuelto(client, "default")

        assert response.status_code == 200
        assert len(_ingestas(session)) == 1

    def test_mensaje_sin_texto_se_ignora_sin_romper(
        self, client: TestClient, session: Session
    ) -> None:
        _canal(session)
        cuerpo = json.dumps(
            {"from": "1@newsletter", "hasMedia": True, "body": "", "timestamp": 1}
        ).encode()

        response = client.post(
            "/webhooks/whatsapp",
            content=cuerpo,
            headers={
                "content-type": "application/json",
                "X-Webhook-Hmac": _firmar("clave-de-prueba", cuerpo),
            },
        )

        assert response.status_code == 200
        assert _ingestas(session) == []

    def test_reintento_del_mismo_mensaje_no_duplica(
        self, client: TestClient, session: Session
    ) -> None:
        _canal(session)
        cuerpo = json.dumps(
            {"from": "1@newsletter", "body": "aceite a 950", "timestamp": 1}
        ).encode()
        headers = {
            "content-type": "application/json",
            "X-Webhook-Hmac": _firmar("clave-de-prueba", cuerpo),
        }

        client.post("/webhooks/whatsapp", content=cuerpo, headers=headers)
        client.post("/webhooks/whatsapp", content=cuerpo, headers=headers)

        assert len(_ingestas(session)) == 1

    def test_el_telefono_se_redacta_igual_que_en_los_otros_caminos(
        self, client: TestClient, session: Session
    ) -> None:
        _canal(session)
        cuerpo = json.dumps(
            {
                "from": "1@newsletter",
                "body": "aceite a 950, llamar al 53439609",
                "timestamp": 1,
            }
        ).encode()

        client.post(
            "/webhooks/whatsapp",
            content=cuerpo,
            headers={
                "content-type": "application/json",
                "X-Webhook-Hmac": _firmar("clave-de-prueba", cuerpo),
            },
        )

        (ingesta,) = _ingestas(session)
        assert "53439609" not in (ingesta.original_text or "")


# --- descarga del archivo -----------------------------------------------------


class TestFetchMedia:
    """Transporte, con `httpx.MockTransport` — nunca un socket real. Mismo
    criterio que `fetch_channel_history` y `eltoque.fetch_rate`."""

    def test_devuelve_los_bytes_del_archivo(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["X-Api-Key"] == "clave-waha"
            return httpx.Response(200, content=b"\xff\xd8bytes-de-la-foto")

        assert (
            fetch_media(
                "http://localhost:3000/api/files/foto.jpg",
                "clave-waha",
                timeout=5.0,
                transport=httpx.MockTransport(handler),
            )
            == b"\xff\xd8bytes-de-la-foto"
        )

    def test_status_distinto_de_200_devuelve_none(self) -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(404))

        assert (
            fetch_media(
                "http://localhost:3000/api/files/foto.jpg",
                "clave-waha",
                timeout=5.0,
                transport=transport,
            )
            is None
        )

    def test_error_de_red_devuelve_none_y_no_levanta(self) -> None:
        """Misma política que el resto del transporte del proyecto: el webhook
        no debe romper porque WAHA no contestó una descarga puntual."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("sin ruta al host")

        assert (
            fetch_media(
                "http://localhost:3000/api/files/foto.jpg",
                "clave-waha",
                timeout=5.0,
                transport=httpx.MockTransport(handler),
            )
            is None
        )

    def test_respuesta_vacia_devuelve_none(self) -> None:
        """Un archivo de cero bytes no es evidencia: crearía una fila cuyo
        `file_path` apunta a nada."""
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b""))

        assert (
            fetch_media(
                "http://localhost:3000/api/files/foto.jpg",
                "clave-waha",
                timeout=5.0,
                transport=transport,
            )
            is None
        )


class TestIsTrustedMediaUrl:
    """La URL viaja DENTRO del payload firmado, así que un tercero no la puede
    inyectar sin la clave HMAC. Aun así se compara contra `waha_base_url`
    antes de pedirla: si la clave se filtra, el webhook no debe convertirse en
    un cliente HTTP que va a donde le digan (SSRF)."""

    def test_la_url_del_waha_configurado_pasa(self) -> None:
        assert (
            is_trusted_media_url(
                "http://localhost:3000/api/files/foto.jpg", "http://localhost:3000"
            )
            is True
        )

    def test_tolera_barra_final_en_la_base(self) -> None:
        assert (
            is_trusted_media_url(
                "http://localhost:3000/api/files/foto.jpg", "http://localhost:3000/"
            )
            is True
        )

    def test_otro_host_no_pasa(self) -> None:
        assert (
            is_trusted_media_url(
                "http://evil.example/api/files/foto.jpg", "http://localhost:3000"
            )
            is False
        )

    def test_otro_puerto_del_mismo_host_no_pasa(self) -> None:
        assert (
            is_trusted_media_url(
                "http://localhost:9999/api/files/foto.jpg", "http://localhost:3000"
            )
            is False
        )

    def test_sin_base_configurada_no_pasa_nada(self) -> None:
        """Cierra en falso, mismo criterio que `require_operator` y que la
        clave HMAC: sin `waha_base_url`, no se descarga."""
        assert is_trusted_media_url("http://localhost:3000/api/files/foto.jpg", None) is False


class TestWebhookFotoSinCaption:
    """El endpoint completo: de la foto de WhatsApp a la fila `image` pendiente.

    `whatsapp_backfill.parse_group_history` midió el 2026-08-18 que 183 de 200
    mensajes de un grupo que publica solo fotos se descartaban acá.
    """

    def _payload_foto(self) -> bytes:
        return json.dumps(
            {
                "from": "1@newsletter",
                "hasMedia": True,
                "body": "",
                "timestamp": 1755000000,
                "media": {
                    "url": "http://localhost:3000/api/files/foto.jpg",
                    "mimetype": "image/jpeg",
                },
            }
        ).encode()

    def _post(self, client: TestClient, cuerpo: bytes) -> httpx.Response:
        return client.post(
            "/webhooks/whatsapp",
            content=cuerpo,
            headers={
                "content-type": "application/json",
                "X-Webhook-Hmac": _firmar("clave-de-prueba", cuerpo),
            },
        )

    def test_la_foto_entra_como_ingesta_de_imagen_pendiente(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        canal = _canal(session)
        monkeypatch.setattr(
            "saimc.web.routers.whatsapp_webhook.fetch_media",
            lambda *a, **k: b"\xff\xd8bytes-de-la-foto",
        )

        response = self._post(client, self._payload_foto())

        assert response.status_code == 200
        (ingesta,) = _ingestas(session)
        assert ingesta.content_type == "image"
        assert ingesta.processing_status == ProcessingStatus.PENDING
        assert ingesta.source_type == SourceType.WHATSAPP_CHANNEL
        assert ingesta.channel_id == canal.id
        # El OCR no corre en la ingesta (spec.md §6.1): el texto queda nulo
        # hasta que el operador lo confirme en la cola.
        assert ingesta.original_text is None

    def test_la_evidencia_queda_escrita_en_el_almacen(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`file_path` tiene que apuntar a un archivo que exista: es la
        evidencia inmutable de la Transparency Layer (spec.md §6.1)."""
        _canal(session)
        monkeypatch.setattr(
            "saimc.web.routers.whatsapp_webhook.fetch_media",
            lambda *a, **k: b"\xff\xd8bytes-de-la-foto",
        )

        self._post(client, self._payload_foto())

        (ingesta,) = _ingestas(session)
        guardado = get_settings().raw_store_dir / ingesta.file_path
        assert guardado.read_bytes() == b"\xff\xd8bytes-de-la-foto"

    def test_la_misma_foto_dos_veces_no_duplica(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dedup por hash de contenido, igual que cualquier otro camino de
        ingesta. Importa acá más que en texto: los canales repiten el mismo
        flyer todos los días."""
        _canal(session)
        monkeypatch.setattr(
            "saimc.web.routers.whatsapp_webhook.fetch_media",
            lambda *a, **k: b"\xff\xd8bytes-de-la-foto",
        )

        self._post(client, self._payload_foto())
        self._post(client, self._payload_foto())

        assert len(_ingestas(session)) == 1

    def test_si_la_descarga_falla_no_rompe_ni_crea_fila(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Degradación agraciada: WAHA caído no debe devolverle un 500 al
        webhook, que lo reintentaría en loop."""
        _canal(session)
        monkeypatch.setattr(
            "saimc.web.routers.whatsapp_webhook.fetch_media", lambda *a, **k: None
        )

        response = self._post(client, self._payload_foto())

        assert response.status_code == 200
        assert _ingestas(session) == []

    def test_la_descarga_corre_fuera_del_event_loop(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Una descarga lenta dentro del `async def` congelaba TODO el servidor
        (2026-10-29: `guani.app` caído por una ráfaga de reintentos de WAHA)."""
        _canal(session)
        en_el_loop: list[bool] = []

        def _espia(*a: object, **k: object) -> bytes:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                en_el_loop.append(False)
            else:
                en_el_loop.append(True)
            return b"\xff\xd8bytes-de-la-foto"

        monkeypatch.setattr("saimc.web.routers.whatsapp_webhook.fetch_media", _espia)

        self._post(client, self._payload_foto())

        assert en_el_loop == [False]

    def test_la_descarga_no_retiene_una_transaccion_de_la_base(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """La búsqueda del canal abre una transacción; si sigue abierta durante
        la descarga, una ráfaga agota el pool de Postgres con conexiones
        `idle in transaction`."""
        _canal(session)
        abierta: list[bool] = []

        def _espia(*a: object, **k: object) -> bytes:
            abierta.append(session.in_transaction())
            return b"\xff\xd8bytes-de-la-foto"

        monkeypatch.setattr("saimc.web.routers.whatsapp_webhook.fetch_media", _espia)

        self._post(client, self._payload_foto())

        assert abierta == [False]
        assert len(_ingestas(session)) == 1

    def test_una_url_de_otro_host_no_se_descarga(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Guarda contra SSRF: aunque la firma HMAC valide, la URL tiene que
        apuntar al WAHA configurado."""
        _canal(session)
        llamadas: list[str] = []

        def _espia(url: str, *a: object, **k: object) -> bytes:
            llamadas.append(url)
            return b"x"

        monkeypatch.setattr("saimc.web.routers.whatsapp_webhook.fetch_media", _espia)
        cuerpo = json.dumps(
            {
                "from": "1@newsletter",
                "hasMedia": True,
                "body": "",
                "timestamp": 1755000000,
                "media": {
                    "url": "http://evil.example/api/files/foto.jpg",
                    "mimetype": "image/jpeg",
                },
            }
        ).encode()

        response = self._post(client, cuerpo)

        assert response.status_code == 200
        assert llamadas == []
        assert _ingestas(session) == []


class TestLoopbackEsElMismoHost:
    """`localhost` y `127.0.0.1` son la misma máquina, y en este despliegue se
    mezclan de verdad.

    WAHA arma `media.url` con el hostname que tiene configurado (típicamente
    `http://localhost:3000/...`), mientras que el `.env` del operador declara
    `SAIMC_WAHA_BASE_URL=http://127.0.0.1:3000`. Comparando las cadenas tal
    cual, TODAS las fotos se descartarían en silencio — el peor modo de falla
    posible para este slice, porque no rompe nada y no captura nada.
    """

    def test_localhost_contra_127_0_0_1(self) -> None:
        assert (
            is_trusted_media_url(
                "http://localhost:3000/api/files/foto.jpg", "http://127.0.0.1:3000"
            )
            is True
        )

    def test_127_0_0_1_contra_localhost(self) -> None:
        assert (
            is_trusted_media_url(
                "http://127.0.0.1:3000/api/files/foto.jpg", "http://localhost:3000"
            )
            is True
        )

    def test_el_puerto_sigue_importando_entre_loopbacks(self) -> None:
        """Otro servicio en la misma máquina no es el WAHA configurado."""
        assert (
            is_trusted_media_url(
                "http://localhost:9999/api/files/foto.jpg", "http://127.0.0.1:3000"
            )
            is False
        )

    def test_un_host_externo_no_se_vuelve_loopback(self) -> None:
        assert (
            is_trusted_media_url(
                "http://evil.example:3000/api/files/foto.jpg", "http://127.0.0.1:3000"
            )
            is False
        )


class TestWebhookLogging:
    """Cada mensaje que NO termina en fila tiene que dejar rastro y motivo.

    Hasta el 2026-08-21 el webhook devolvía 200 con `{"ingested": 0, "reason":
    ...}` en todos los caminos de descarte y no registraba nada. El canal
    «Tienda: ⚜️ The Boss» terminó con 28 filas de texto y 1 sola imagen
    mientras WAHA tenía las fotos, y no hubo forma de saber por cuál camino se
    fueron: el journal solo mostraba `POST /webhooks/whatsapp 200 OK`.
    `whatsapp_media` ya avisaba de este modo de falla — «no rompe nada y no
    captura nada».

    Nivel según lo que significa cada caso: WARNING cuando se pierde algo que
    debía entrar, INFO cuando el no-ingreso es lo esperado (el duplicado del
    par de eventos que WAHA manda por cada mensaje).
    """

    _LOGGER = "saimc.web.routers.whatsapp_webhook"

    def _post(self, client: TestClient, cuerpo: bytes) -> httpx.Response:
        return client.post(
            "/webhooks/whatsapp",
            content=cuerpo,
            headers={
                "content-type": "application/json",
                "X-Webhook-Hmac": _firmar("clave-de-prueba", cuerpo),
            },
        )

    def _payload_texto(self, body: str = "aceite 1L a 950") -> bytes:
        return json.dumps(
            {"from": "1@newsletter", "body": body, "timestamp": 1755000000}
        ).encode()

    def _payload_foto(self, url: str) -> bytes:
        return json.dumps(
            {
                "from": "1@newsletter",
                "hasMedia": True,
                "body": "",
                "timestamp": 1755000000,
                "media": {"url": url, "mimetype": "image/jpeg"},
            }
        ).encode()

    def test_el_ingreso_deja_rastro(
        self, client: TestClient, session: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        _canal(session)
        with caplog.at_level(logging.INFO, logger=self._LOGGER):
            self._post(client, self._payload_texto())

        assert any("webhook.ingerido" in r.message for r in caplog.records)

    def test_canal_no_registrado_deja_rastro_con_el_jid(
        self, client: TestClient, session: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Sin el JID en el log no hay forma de dar de alta el canal que falta."""
        with caplog.at_level(logging.INFO, logger=self._LOGGER):
            self._post(client, self._payload_texto())

        registro = next(r for r in caplog.records if "webhook.descartado" in r.message)
        assert "canal_no_registrado" in registro.message
        assert "1@newsletter" in registro.message

    def test_mensaje_sin_nada_utilizable_deja_rastro(
        self, client: TestClient, session: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        _canal(session)
        cuerpo = json.dumps(
            {
                "from": "1@newsletter",
                "body": "",
                "hasMedia": True,
                "timestamp": 1755000000,
            }
        ).encode()

        with caplog.at_level(logging.INFO, logger=self._LOGGER):
            self._post(client, cuerpo)

        registro = next(r for r in caplog.records if "webhook.descartado" in r.message)
        assert "sin_mensaje_utilizable" in registro.message
        assert registro.levelno == logging.WARNING

    def test_la_foto_que_no_se_pudo_bajar_dice_por_que(
        self,
        client: TestClient,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """El motivo concreto importa: no es lo mismo que WAHA no respondiera
        que que la URL no apunte al WAHA configurado."""
        _canal(session)
        monkeypatch.setattr(
            "saimc.web.routers.whatsapp_webhook.fetch_media", lambda *a, **k: None
        )

        with caplog.at_level(logging.INFO, logger=self._LOGGER):
            self._post(
                client, self._payload_foto("http://localhost:3000/api/files/f.jpg")
            )

        mensajes = " | ".join(r.message for r in caplog.records)
        assert "descarga_fallida" in mensajes

    def test_la_url_que_no_es_del_waha_configurado_dice_por_que(
        self, client: TestClient, session: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        _canal(session)

        with caplog.at_level(logging.INFO, logger=self._LOGGER):
            self._post(client, self._payload_foto("http://evil.example.com/f.jpg"))

        mensajes = " | ".join(r.message for r in caplog.records)
        assert "media_url_no_confiable" in mensajes

    def test_el_duplicado_es_info_y_no_warning(
        self, client: TestClient, session: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        """WAHA manda DOS eventos por mensaje (verificado en el journal del VPS:
        los POST llegan siempre de a pares). El segundo deduplica por hash: es
        lo esperado, no una pérdida, y a nivel WARNING sería la mitad del log.
        """
        _canal(session)
        self._post(client, self._payload_texto())

        with caplog.at_level(logging.INFO, logger=self._LOGGER):
            self._post(client, self._payload_texto())

        registro = next(r for r in caplog.records if "duplicado" in r.message)
        assert registro.levelno == logging.INFO

    def test_no_se_registra_el_texto_del_mensaje(
        self, client: TestClient, session: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        """El log no es la capa pública, pero el cuerpo puede traer teléfonos y
        el proyecto no almacena datos de contacto (spec.md §10.6). Se registran
        metadatos: JID, marca de tiempo, si traía adjunto y el motivo.
        """
        _canal(session)
        secreto = "llamame al 53123456 por el pollo"

        with caplog.at_level(logging.INFO, logger=self._LOGGER):
            self._post(client, self._payload_texto(secreto))

        assert not any("53123456" in r.message for r in caplog.records)
