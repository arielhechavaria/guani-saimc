"""Emisor de grupo como hash con clave (slice B5, `docs/spec-slice-B5.md`).

Los once casos de «Tests primero» de la especificación, escritos antes que el
código. Los valores esperados salen de la especificación y se calculan acá a
mano, con `hmac` de la biblioteca estándar sobre la cadena YA normalizada
escrita como literal: así la normalización queda fijada por el test y no por
lo que haga la función bajo prueba.

Los números de teléfono de este archivo son inventados.
"""

import hashlib
import hmac
import importlib.util
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.ingestion import whatsapp_backfill
from saimc.ingestion.sender import sender_hash, warn_if_key_too_short
from saimc.ingestion.whatsapp_backfill import parse_group_history, parse_history
from saimc.ingestion.whatsapp_webhook import parse_message
from saimc.models import Channel, Platform, RawIngestion
from saimc.web.app import app
from saimc.web.auth import require_operator
from saimc.web.deps import get_db

# 64 caracteres, como la que genera `secrets.token_hex(32)`.
CLAVE = "0123456789abcdef" * 4
OTRA_CLAVE = "fedcba9876543210" * 4
HMAC_WEBHOOK = "clave-de-prueba"

NUMERO = "5355512345"
JID_EMISOR = f"{NUMERO}@c.us"
GRUPO_A = "120363000000000001@g.us"
GRUPO_B = "120363000000000002@g.us"
CANAL = "120363000000000003@newsletter"

# El oráculo: HMAC-SHA256 de la cadena normalizada, escrita a mano.
ESPERADO = hmac.new(CLAVE.encode(), b"5355512345@c.us", hashlib.sha256).hexdigest()
ESPERADO_LID = hmac.new(CLAVE.encode(), b"5355512345@lid", hashlib.sha256).hexdigest()


# --- casos 1 a 4: la función pura ---------------------------------------------


class TestSenderHash:
    def test_mismo_jid_y_misma_clave_dan_el_mismo_hash_de_64_hex(self) -> None:
        """Caso 1."""
        primero = sender_hash(JID_EMISOR, CLAVE)
        segundo = sender_hash(JID_EMISOR, CLAVE)

        assert primero == segundo == ESPERADO
        assert primero is not None
        assert len(primero) == 64
        assert set(primero) <= set("0123456789abcdef")

    def test_otra_clave_da_otro_hash(self) -> None:
        """Caso 2."""
        esperado_otra = hmac.new(
            OTRA_CLAVE.encode(), b"5355512345@c.us", hashlib.sha256
        ).hexdigest()

        assert sender_hash(JID_EMISOR, OTRA_CLAVE) == esperado_otra
        assert sender_hash(JID_EMISOR, OTRA_CLAVE) != sender_hash(JID_EMISOR, CLAVE)

    @pytest.mark.parametrize(
        "jid",
        [
            "5355512345@c.us",
            "5355512345@s.whatsapp.net",
            "5355512345:3@s.whatsapp.net",
            # «minúsculas» en la especificación.
            "5355512345@C.US",
            "5355512345@S.WhatsApp.Net",
        ],
    )
    def test_las_formas_del_mismo_numero_dan_el_mismo_hash(self, jid: str) -> None:
        """Caso 3: el webhook y el backfill tienen que coincidir."""
        assert sender_hash(jid, CLAVE) == ESPERADO

    def test_el_identificador_interno_lid_da_otro_hash(self) -> None:
        """Caso 3, segunda mitad: `@lid` no se une con `@c.us`."""
        assert sender_hash("5355512345@lid", CLAVE) == ESPERADO_LID
        assert ESPERADO_LID != ESPERADO

    def test_sin_clave_es_nulo(self) -> None:
        """Caso 4."""
        assert sender_hash(JID_EMISOR, None) is None
        assert sender_hash(JID_EMISOR, "") is None

    def test_clave_de_31_caracteres_es_nulo_y_de_32_no(self) -> None:
        """Caso 4, en el borde: 32 es el mínimo."""
        assert sender_hash(JID_EMISOR, "k" * 31) is None
        assert sender_hash(JID_EMISOR, "k" * 32) == hmac.new(
            b"k" * 32, b"5355512345@c.us", hashlib.sha256
        ).hexdigest()

    @pytest.mark.parametrize("jid", [None, "", "   ", 5355512345, {"user": NUMERO}])
    def test_sin_emisor_reconocible_es_nulo(self, jid: object) -> None:
        assert sender_hash(jid, CLAVE) is None


# --- el parser: el JID no sale de `parse_message` ----------------------------


def _mensaje_de_grupo(
    *, grupo: str = GRUPO_A, emisor: str = JID_EMISOR, body: str = "aceite 1L a 950"
) -> dict[str, object]:
    return {
        "from": grupo,
        "participant": emisor,
        "fromMe": False,
        "body": body,
        "timestamp": 1755000000,
    }


class TestParseMessage:
    def test_mensaje_de_grupo_lleva_el_hash_del_emisor(self) -> None:
        mensaje = parse_message(_mensaje_de_grupo(), sender_key=CLAVE)

        assert mensaje is not None
        assert mensaje.chat_id == GRUPO_A
        assert mensaje.sender_hash == ESPERADO

    def test_envuelto_en_event_y_payload_tambien(self) -> None:
        cuerpo = {"event": "message", "session": "default", "payload": _mensaje_de_grupo()}

        mensaje = parse_message(cuerpo, sender_key=CLAVE)

        assert mensaje is not None
        assert mensaje.sender_hash == ESPERADO

    def test_sin_clave_el_mensaje_se_parsea_igual_con_hash_nulo(self) -> None:
        """Caso 4."""
        mensaje = parse_message(_mensaje_de_grupo())

        assert mensaje is not None
        assert mensaje.text == "aceite 1L a 950"
        assert mensaje.sender_hash is None

    def test_canal_de_difusion_sin_participant_da_nulo(self) -> None:
        """Caso 6."""
        mensaje = parse_message(
            {"from": CANAL, "body": "aceite 1L a 950", "timestamp": 1755000000},
            sender_key=CLAVE,
        )

        assert mensaje is not None
        assert mensaje.sender_hash is None

    def test_participant_solo_se_lee_cuando_from_es_un_grupo(self) -> None:
        """La especificación: «solo cuando `from` es un grupo (`@g.us`)»."""
        mensaje = parse_message(
            {
                "from": CANAL,
                "participant": JID_EMISOR,
                "body": "aceite 1L a 950",
                "timestamp": 1755000000,
            },
            sender_key=CLAVE,
        )

        assert mensaje is not None
        assert mensaje.sender_hash is None

    def test_repr_no_contiene_el_jid_ni_el_hash(self) -> None:
        """Caso 9."""
        mensaje = parse_message(_mensaje_de_grupo(), sender_key=CLAVE)

        assert mensaje is not None
        assert mensaje.sender_hash == ESPERADO
        texto = repr(mensaje)
        assert NUMERO not in texto
        assert ESPERADO not in texto
        assert "sender" not in texto
        # Ningún atributo del mensaje parseado lleva el JID en claro.
        assert all(NUMERO not in str(valor) for valor in vars(mensaje).values())


class TestParseDelHistorico:
    def test_el_historico_de_un_grupo_lleva_el_hash_en_cada_mensaje(self) -> None:
        """Caso 7, mitad pura."""
        entradas = [
            _mensaje_de_grupo(body="pollo a 500"),
            _mensaje_de_grupo(body="arroz a 300", emisor="5355512345:3@s.whatsapp.net"),
            _mensaje_de_grupo(body="aceite a 950", emisor="5355512345@lid"),
        ]

        resultado = parse_group_history(entradas, sender_key=CLAVE)

        assert [m.sender_hash for m in resultado] == [ESPERADO, ESPERADO, ESPERADO_LID]

    def test_el_historico_de_un_canal_queda_sin_emisor(self) -> None:
        entradas = [{"message": {"from": CANAL, "body": "pollo a 500", "timestamp": 1}}]

        resultado = parse_history(entradas, sender_key=CLAVE)

        assert len(resultado) == 1
        assert resultado[0].sender_hash is None


# --- casos 5, 6, 8 y 10: el webhook -------------------------------------------


def _firmar(body: bytes) -> str:
    return hmac.new(HMAC_WEBHOOK.encode(), body, hashlib.sha512).hexdigest()


def _canal(session: Session, external_id: str, name: str) -> Channel:
    plataforma = (
        Platform.WHATSAPP_GROUP if external_id.endswith("@g.us") else Platform.WHATSAPP_CHANNEL
    )
    canal = Channel(name=name, platform=plataforma, external_id=external_id, is_active=True)
    session.add(canal)
    session.commit()
    return canal


def _ingestas(session: Session) -> list[RawIngestion]:
    return list(session.execute(select(RawIngestion).order_by(RawIngestion.original_text)).scalars())


def _post(client: TestClient, payload: dict[str, object]) -> Any:
    cuerpo = json.dumps(payload).encode()
    return client.post(
        "/webhooks/whatsapp",
        content=cuerpo,
        headers={"content-type": "application/json", "X-Webhook-Hmac": _firmar(cuerpo)},
    )


def _entorno(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, clave: str) -> None:
    (tmp_path / "inbox").mkdir(exist_ok=True)
    monkeypatch.setenv("SAIMC_INBOX_DIR", str(tmp_path / "inbox"))
    monkeypatch.setenv("SAIMC_RAW_STORE_DIR", str(tmp_path / "data" / "raw"))
    monkeypatch.setenv("SAIMC_WHATSAPP_WEBHOOK_HMAC_KEY", HMAC_WEBHOOK)
    monkeypatch.setenv("SAIMC_WAHA_BASE_URL", "http://127.0.0.1:3000")
    monkeypatch.setenv("SAIMC_WAHA_API_KEY", "clave-waha")
    # setenv("") y no delenv: pydantic-settings también lee el `.env` del disco.
    monkeypatch.setenv("SAIMC_SENDER_HASH_KEY", clave)
    get_settings.cache_clear()


@pytest.fixture
def client(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    _entorno(monkeypatch, tmp_path, CLAVE)
    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[require_operator] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()
    get_settings.cache_clear()


class TestWebhook:
    def test_mensaje_de_grupo_deja_la_fila_con_su_sender_hash(
        self, client: TestClient, session: Session
    ) -> None:
        """Caso 5."""
        _canal(session, GRUPO_A, "Mercado A")

        response = _post(client, _mensaje_de_grupo())

        assert response.json() == {"ingested": 1}
        (fila,) = _ingestas(session)
        assert fila.sender_hash == ESPERADO

    def test_mensaje_de_canal_de_difusion_deja_sender_hash_nulo(
        self, client: TestClient, session: Session
    ) -> None:
        """Caso 6."""
        _canal(session, CANAL, "Tienda")

        response = _post(
            client, {"from": CANAL, "body": "aceite 1L a 950", "timestamp": 1755000000}
        )

        assert response.json() == {"ingested": 1}
        (fila,) = _ingestas(session)
        assert fila.sender_hash is None

    @pytest.mark.parametrize("clave", ["", "corta", "k" * 31])
    def test_sin_clave_o_con_clave_corta_se_ingiere_igual_con_nulo(
        self,
        client: TestClient,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        clave: str,
    ) -> None:
        """Caso 4: la falta de clave nunca frena una ingesta."""
        _entorno(monkeypatch, tmp_path, clave)
        _canal(session, GRUPO_A, "Mercado A")

        response = _post(client, _mensaje_de_grupo())

        assert response.status_code == 200
        assert response.json() == {"ingested": 1}
        (fila,) = _ingestas(session)
        assert fila.sender_hash is None
        assert "aceite 1L a 950" in (fila.original_text or "")

    def test_el_mismo_emisor_en_dos_grupos_comparte_sender_hash(
        self, client: TestClient, session: Session
    ) -> None:
        """Caso 10: global, no por grupo."""
        canal_a = _canal(session, GRUPO_A, "Mercado A")
        canal_b = _canal(session, GRUPO_B, "Mercado B")

        _post(client, _mensaje_de_grupo(grupo=GRUPO_A, body="aceite 1L a 950"))
        _post(
            client,
            _mensaje_de_grupo(
                grupo=GRUPO_B, body="pollo a 500", emisor="5355512345:7@s.whatsapp.net"
            ),
        )
        _post(
            client,
            _mensaje_de_grupo(grupo=GRUPO_B, body="zzz arroz a 300", emisor="5350000000@c.us"),
        )

        aceite, pollo, arroz = _ingestas(session)
        assert {aceite.channel_id, pollo.channel_id} == {canal_a.id, canal_b.id}
        assert aceite.sender_hash == pollo.sender_hash == ESPERADO
        # Control: otro vendedor del mismo grupo NO comparte el hash.
        assert arroz.sender_hash is not None
        assert arroz.sender_hash != ESPERADO

    def test_el_jid_no_queda_en_la_fila_ni_en_el_archivo_ni_en_los_logs(
        self,
        client: TestClient,
        session: Session,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
    ) -> None:
        """Caso 8. Se recorren los tres caminos del webhook que registran algo:
        el ingreso, el duplicado y el grupo no registrado."""
        _canal(session, GRUPO_A, "Mercado A")

        with caplog.at_level(logging.DEBUG):
            ingreso = _post(client, _mensaje_de_grupo())
            _post(client, _mensaje_de_grupo())  # duplicado
            _post(client, _mensaje_de_grupo(grupo=GRUPO_B))  # grupo no registrado
            # Sin nada utilizable: ni texto ni foto.
            _post(client, {"from": GRUPO_A, "participant": JID_EMISOR, "body": ""})

        assert ingreso.json() == {"ingested": 1}
        assert caplog.records, "el webhook tiene que haber registrado algo"
        registrado = caplog.text + "\n".join(r.getMessage() for r in caplog.records)
        assert NUMERO not in registrado
        assert ESPERADO not in registrado

        (fila,) = _ingestas(session)
        assert fila.sender_hash == ESPERADO
        for columna in RawIngestion.__table__.columns:
            assert NUMERO not in str(getattr(fila, columna.name)), columna.name

        almacen = tmp_path / "data" / "raw"
        guardados = [ruta for ruta in almacen.rglob("*") if ruta.is_file()]
        assert [ruta.name for ruta in guardados] == [fila.file_path]
        for ruta in guardados:
            assert NUMERO.encode() not in ruta.read_bytes()
            assert NUMERO not in ruta.name
        # Y nada quedó tirado en la bandeja con el emisor adentro.
        for ruta in (tmp_path / "inbox").rglob("*"):
            if ruta.is_file():
                assert NUMERO.encode() not in ruta.read_bytes()

    def test_repr_de_la_fila_no_contiene_el_hash(
        self, client: TestClient, session: Session
    ) -> None:
        """Caso 9."""
        _canal(session, GRUPO_A, "Mercado A")
        _post(client, _mensaje_de_grupo())

        (fila,) = _ingestas(session)
        assert fila.sender_hash == ESPERADO
        assert ESPERADO not in repr(fila)
        assert NUMERO not in repr(fila)


# --- caso 7: el backfill de un grupo, de punta a punta ------------------------


class TestBackfillDeGrupo:
    def test_el_backfill_de_un_grupo_deja_el_sender_hash_en_cada_fila(
        self,
        client: TestClient,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Caso 7. Se reemplaza solo el transporte HTTP: el parser, el router y
        la ingesta son los reales, así que la clave tiene que llegar sola desde
        la configuración hasta `parse_message`."""
        canal = _canal(session, GRUPO_A, "Mercado A")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    _mensaje_de_grupo(body="pollo a 500"),
                    _mensaje_de_grupo(body="zzz arroz a 300", emisor="5350000000@c.us"),
                ],
            )

        real = whatsapp_backfill.fetch_channel_history
        monkeypatch.setattr(
            "saimc.web.routers.catalog.fetch_channel_history",
            lambda *a, **k: real(*a, **k, transport=httpx.MockTransport(handler)),
        )

        with caplog.at_level(logging.DEBUG):
            response = client.post(
                f"/catalog/channels/{canal.id}/backfill", follow_redirects=False
            )

        assert response.status_code == 303
        assert "error=" not in response.headers["location"]
        pollo, arroz = _ingestas(session)
        assert pollo.sender_hash == ESPERADO
        assert arroz.sender_hash == hmac.new(
            CLAVE.encode(), b"5350000000@c.us", hashlib.sha256
        ).hexdigest()
        assert pollo.channel_id == arroz.channel_id == canal.id
        # Caso 8 también por este camino.
        assert NUMERO not in caplog.text
        for columna in RawIngestion.__table__.columns:
            assert NUMERO not in str(getattr(pollo, columna.name)), columna.name


# --- la clave corta se avisa al arrancar, sin mostrarla -----------------------


class TestAvisoDeClaveCorta:
    def test_una_clave_corta_avisa_sin_registrar_la_clave(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG):
            warn_if_key_too_short("clave-corta-secreta")

        avisos = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(avisos) == 1
        assert "SAIMC_SENDER_HASH_KEY" in avisos[0].getMessage()
        assert "clave-corta-secreta" not in caplog.text

    def test_el_arranque_de_la_aplicacion_emite_el_aviso(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """El aviso está enchufado al arranque real, no solo definido."""
        _entorno(monkeypatch, tmp_path, "clave-corta-secreta")

        # Entrar al `TestClient` con `with` es lo que corre el arranque (lifespan).
        with caplog.at_level(logging.DEBUG), TestClient(app):
            pass
        get_settings.cache_clear()

        assert any("SAIMC_SENDER_HASH_KEY" in r.getMessage() for r in caplog.records)
        assert "clave-corta-secreta" not in caplog.text

    @pytest.mark.parametrize("clave", [None, "", CLAVE])
    def test_sin_clave_o_con_clave_valida_no_avisa(
        self, clave: str | None, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Sin clave es un estado soportado, no un error de configuración."""
        with caplog.at_level(logging.DEBUG):
            warn_if_key_too_short(clave)

        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# --- caso 11: la migración ----------------------------------------------------


def _cargar_migracion(nombre: str) -> ModuleType:
    ruta = Path(__file__).resolve().parent.parent / "alembic" / "versions" / nombre
    spec = importlib.util.spec_from_file_location(nombre.removesuffix(".py"), ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


class TestMigracion0029:
    def test_sube_y_baja(self) -> None:
        """Caso 11."""
        modulo = _cargar_migracion("0029_raw_ingestions_sender_hash.py")
        assert modulo.revision == "0029"
        assert modulo.down_revision == "0028"

        engine = create_engine("sqlite://")
        metadata = sa.MetaData()
        sa.Table(
            "raw_ingestions",
            metadata,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column("content_hash", sa.String(64), nullable=False),
        )
        metadata.create_all(engine)

        with engine.connect() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                modulo.upgrade()
            conn.commit()
            insp = inspect(conn)
            columnas = {c["name"]: c for c in insp.get_columns("raw_ingestions")}
            assert columnas["sender_hash"]["nullable"] is True
            tipo = columnas["sender_hash"]["type"]
            assert isinstance(tipo, sa.String)
            assert tipo.length == 64
            indexadas = [tuple(i["column_names"]) for i in insp.get_indexes("raw_ingestions")]
            assert ("sender_hash",) in indexadas

        with engine.connect() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                modulo.downgrade()
            conn.commit()
            insp = inspect(conn)
            assert {c["name"] for c in insp.get_columns("raw_ingestions")} == {
                "id",
                "content_hash",
            }
            assert insp.get_indexes("raw_ingestions") == []
        engine.dispose()

    def test_el_modelo_declara_la_misma_columna_con_indice(self) -> None:
        tabla = RawIngestion.__table__
        assert isinstance(tabla, sa.Table)
        columna = tabla.columns["sender_hash"]

        assert columna.nullable is True
        assert isinstance(columna.type, sa.String)
        assert columna.type.length == 64
        assert any(
            [c.name for c in indice.columns] == ["sender_hash"] for indice in tabla.indexes
        )
