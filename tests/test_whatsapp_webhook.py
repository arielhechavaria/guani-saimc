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

import hashlib
import hmac
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.ingestion.whatsapp_webhook import parse_message, verify_hmac
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

    def test_sin_texto_ni_caption_se_descarta(self) -> None:
        """Media sin texto (solo una foto, sin caption): fuera de alcance de
        este slice — no hay OCR desde el webhook todavía. Se descarta en vez
        de crear una fila vacía."""
        payload = {"from": "1@newsletter", "hasMedia": True, "body": "", "timestamp": 1}

        assert parse_message(payload) is None

    def test_sin_chat_id_se_descarta(self) -> None:
        assert parse_message({"body": "aceite a 950", "timestamp": 1}) is None

    def test_payload_no_es_diccionario_se_descarta(self) -> None:
        assert parse_message("no soy un json valido") is None  # type: ignore[arg-type]


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
