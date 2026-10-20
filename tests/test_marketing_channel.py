"""Publicación saliente al canal de WhatsApp de Guani (`saimc.marketing.channel`).

Los casos esperados se escriben antes que el código: qué se manda (solo lo
aprobado del día y del turno), en qué forma (la imagen en base64, el texto
solo en la primera lámina y sin hashtags), y qué pasa cuando algo falla a
mitad (se guarda lo enviado y la repetición sigue desde la lámina siguiente).

WAHA no existe acá: `httpx.MockTransport` hace de servidor y anota lo que le
llegó. Nada sale a la red.
"""

from __future__ import annotations

import base64
import importlib.util
import json
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from saimc.marketing.channel import (
    ChannelConfig,
    ChannelSendError,
    build_channel_text,
    main,
    run_channel,
    send_image,
)
from saimc.models import MarketingPost, MarketingPostSlide
from saimc.models.marketing_post import PostOrigin, PostStatus

DIA = date(2026, 10, 20)
CONFIG = ChannelConfig(
    base_url="http://127.0.0.1:3000",
    api_key="clave-waha",
    session="guani-canal",
    chat_id="120363000000000000@newsletter",
    timeout=5.0,
    ingest_session="default",
)
CAPTION = (
    "Los datos del día: Arroz, Aceite.\n\nFuente: canales.\n\n#guani #preciosencuba #cuba #mercadocubano"
)


# --- build_channel_text -----------------------------------------------------------


class TestBuildChannelText:
    def test_quita_la_linea_final_de_hashtags_del_pie_de_instagram(self) -> None:
        assert build_channel_text(CAPTION) == "Los datos del día: Arroz, Aceite.\n\nFuente: canales."

    def test_quita_los_hashtags_al_final_de_una_linea_con_texto(self) -> None:
        assert build_channel_text("Precio del arroz hoy #cuba #precios") == "Precio del arroz hoy"

    def test_deja_un_hashtag_en_medio_de_la_frase(self) -> None:
        """En medio de la frase es una palabra: sacarlo rompe la oración."""
        assert build_channel_text("El #arroz subió esta semana.") == "El #arroz subió esta semana."

    def test_un_numeral_seguido_de_numero_no_es_hashtag(self) -> None:
        assert build_channel_text("Lote #3") == "Lote #3"

    def test_no_deja_lineas_en_blanco_de_mas_en_el_medio(self) -> None:
        texto = "Uno.\n\n#guani #cuba\n\nDos."

        assert build_channel_text(texto) == "Uno.\n\nDos."

    def test_un_pie_que_es_solo_hashtags_queda_vacio(self) -> None:
        assert build_channel_text("#guani #cuba") == ""


# --- send_image ---------------------------------------------------------------------


class Waha:
    """WAHA falso: anota cada envío y responde lo que se le pida."""

    def __init__(self, fallar_en: int | None = None, status: int = 201) -> None:
        self.envios: list[dict] = []
        self.headers: list[httpx.Headers] = []
        self.urls: list[str] = []
        self.fallar_en = fallar_en
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        numero = len(self.envios) + 1
        if self.fallar_en is not None and numero == self.fallar_en:
            return httpx.Response(500, json={"error": "boom"})
        self.envios.append(json.loads(request.content))
        self.headers.append(request.headers)
        self.urls.append(str(request.url))
        return httpx.Response(self.status, json={"id": f"msg-{numero}"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


class TestSendImage:
    def test_manda_el_jpeg_en_base64_a_la_sesion_y_el_canal(self) -> None:
        waha = Waha()

        send_image(CONFIG, jpeg=b"JPEG1", filename="guani-1.jpg", caption="Hola", transport=waha.transport)

        assert waha.urls == ["http://127.0.0.1:3000/api/sendImage"]
        assert waha.headers[0]["X-Api-Key"] == "clave-waha"
        assert waha.envios[0] == {
            "session": "guani-canal",
            "chatId": "120363000000000000@newsletter",
            "file": {
                "mimetype": "image/jpeg",
                "filename": "guani-1.jpg",
                "data": base64.b64encode(b"JPEG1").decode(),
            },
            "caption": "Hola",
        }

    def test_sin_texto_no_manda_caption(self) -> None:
        waha = Waha()

        send_image(CONFIG, jpeg=b"J", filename="x.jpg", caption=None, transport=waha.transport)

        assert "caption" not in waha.envios[0]

    def test_un_status_de_error_levanta(self) -> None:
        with pytest.raises(ChannelSendError, match="500"):
            send_image(
                CONFIG, jpeg=b"J", filename="x.jpg", caption=None, transport=Waha(fallar_en=1).transport
            )

    def test_un_error_de_red_levanta(self) -> None:
        def caido(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no responde", request=request)

        with pytest.raises(ChannelSendError):
            send_image(
                CONFIG, jpeg=b"J", filename="x.jpg", caption=None, transport=httpx.MockTransport(caido)
            )


# --- run_channel ---------------------------------------------------------------------


def _post(
    session: Session,
    images_dir: Path,
    *,
    slot: str = "datos",
    status: str = PostStatus.APPROVED,
    day: date = DIA,
    slides: int = 3,
    caption: str = CAPTION,
) -> MarketingPost:
    """Una publicación del lote con `slides` láminas y sus JPEG en disco."""
    post = MarketingPost(
        format="carousel" if slides > 1 else "single",
        status=status,
        planned_for=day,
        caption=caption,
        origin=PostOrigin.BATCH,
        batch_slot=slot,
        approved_at=(
            datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
            if status in (PostStatus.APPROVED, PostStatus.PUBLISHED)
            else None
        ),
        published_url="https://instagram.com/p/x" if status == PostStatus.PUBLISHED else None,
        published_at=(datetime(2026, 10, 20, 9, 5, tzinfo=UTC) if status == PostStatus.PUBLISHED else None),
        # `ck_marketing_posts_discarded_at`: una descartada sin fecha no entra a la base.
        discarded_at=(datetime(2026, 10, 20, 8, 0, tzinfo=UTC) if status == PostStatus.DISCARDED else None),
    )
    session.add(post)
    session.flush()
    carpeta = images_dir / str(post.id)
    carpeta.mkdir(parents=True, exist_ok=True)
    for n in range(1, slides + 1):
        aprobada = status in (PostStatus.APPROVED, PostStatus.PUBLISHED)
        if aprobada:
            (carpeta / f"{n}.jpg").write_bytes(f"JPEG{n}".encode())
        session.add(
            MarketingPostSlide(
                post_id=post.id,
                position=n,
                card_slug="datos-portada",
                params={},
                options={},
                snapshot={"version": 1} if aprobada else None,
                image_path=f"{post.id}/{n}.jpg" if aprobada else None,
            )
        )
    session.commit()
    session.refresh(post)
    return post


def _correr(
    session: Session,
    images_dir: Path,
    waha: Waha,
    *,
    execute: bool = True,
    config: ChannelConfig = CONFIG,
    slot: str = "datos",
):
    pausas: list[float] = []
    reporte = run_channel(
        session,
        DIA,
        slot,
        images_dir,
        execute=execute,
        config=config,
        transport=waha.transport,
        sleep=pausas.append,
        now=datetime(2026, 10, 20, 10, 0, tzinfo=UTC),
    )
    return reporte, pausas


class TestRunChannel:
    def test_manda_cada_lamina_en_orden_con_el_texto_solo_en_la_primera(
        self, session: Session, tmp_path: Path
    ) -> None:
        post = _post(session, tmp_path)
        waha = Waha()

        reporte, pausas = _correr(session, tmp_path, waha)

        assert reporte.exit_code == 0
        assert [base64.b64decode(e["file"]["data"]) for e in waha.envios] == [
            b"JPEG1",
            b"JPEG2",
            b"JPEG3",
        ]
        assert waha.envios[0]["caption"] == "Los datos del día: Arroz, Aceite.\n\nFuente: canales."
        assert all("caption" not in e for e in waha.envios[1:])
        # Una pausa entre láminas, no antes de la primera.
        assert pausas == [1.5, 1.5]
        session.refresh(post)
        assert post.channel_slides_sent == 3
        assert post.channel_sent_at is not None

    def test_en_seco_no_llama_a_waha_ni_escribe(self, session: Session, tmp_path: Path) -> None:
        post = _post(session, tmp_path)
        waha = Waha()

        reporte, _ = _correr(session, tmp_path, waha, execute=False)

        assert waha.envios == []
        assert reporte.exit_code == 0
        session.refresh(post)
        assert post.channel_slides_sent == 0
        assert post.channel_sent_at is None

    def test_sin_nada_aprobado_no_manda_y_sale_con_1(self, session: Session, tmp_path: Path) -> None:
        _post(session, tmp_path, status=PostStatus.DRAFT)
        _post(session, tmp_path, status=PostStatus.DISCARDED, slot="educativo")
        waha = Waha()

        reporte, _ = _correr(session, tmp_path, waha)

        assert waha.envios == []
        assert reporte.exit_code == 1
        assert "nada aprobado" in reporte.message

    def test_solo_manda_el_turno_y_el_dia_pedidos(self, session: Session, tmp_path: Path) -> None:
        _post(session, tmp_path, slot="educativo", slides=1)
        _post(session, tmp_path, day=date(2026, 10, 19), slides=1)
        datos = _post(session, tmp_path, slides=1)
        waha = Waha()

        reporte, _ = _correr(session, tmp_path, waha)

        assert reporte.exit_code == 0
        assert len(waha.envios) == 1
        session.refresh(datos)
        assert datos.channel_sent_at is not None

    def test_una_ya_marcada_publicada_en_instagram_tambien_sale(
        self, session: Session, tmp_path: Path
    ) -> None:
        """Publicada implica aprobada: si el operador la subió a Instagram antes
        del envío al canal, el canal no la pierde."""
        _post(session, tmp_path, status=PostStatus.PUBLISHED, slides=1)
        waha = Waha()

        reporte, _ = _correr(session, tmp_path, waha)

        assert reporte.exit_code == 0
        assert len(waha.envios) == 1

    def test_un_error_a_mitad_guarda_lo_enviado_y_sale_con_1(self, session: Session, tmp_path: Path) -> None:
        post = _post(session, tmp_path)
        waha = Waha(fallar_en=3)

        reporte, _ = _correr(session, tmp_path, waha)

        assert reporte.exit_code == 1
        assert "500" in reporte.message
        session.refresh(post)
        assert post.channel_slides_sent == 2
        assert post.channel_sent_at is None

    def test_la_repeticion_sigue_desde_la_lamina_siguiente_sin_texto(
        self, session: Session, tmp_path: Path
    ) -> None:
        post = _post(session, tmp_path)
        _correr(session, tmp_path, Waha(fallar_en=3))
        waha = Waha()

        reporte, _ = _correr(session, tmp_path, waha)

        assert reporte.exit_code == 0
        assert [base64.b64decode(e["file"]["data"]) for e in waha.envios] == [b"JPEG3"]
        assert "caption" not in waha.envios[0]
        session.refresh(post)
        assert post.channel_slides_sent == 3
        assert post.channel_sent_at is not None

    def test_una_ya_enviada_no_se_repite(self, session: Session, tmp_path: Path) -> None:
        _post(session, tmp_path)
        _correr(session, tmp_path, Waha())
        waha = Waha()

        reporte, _ = _correr(session, tmp_path, waha)

        assert waha.envios == []
        assert reporte.exit_code == 0
        assert "ya enviada" in reporte.message

    def test_sin_canal_configurado_no_manda_y_sale_con_1(self, session: Session, tmp_path: Path) -> None:
        _post(session, tmp_path)
        waha = Waha()
        sin_canal = ChannelConfig(**{**CONFIG.__dict__, "chat_id": ""})

        reporte, _ = _correr(session, tmp_path, waha, config=sin_canal)

        assert waha.envios == []
        assert reporte.exit_code == 1
        assert "SAIMC_WAHA_CHANNEL_ID" in reporte.message

    def test_sin_url_o_clave_de_waha_no_manda_y_sale_con_1(self, session: Session, tmp_path: Path) -> None:
        _post(session, tmp_path)
        waha = Waha()
        sin_clave = ChannelConfig(**{**CONFIG.__dict__, "api_key": None})

        reporte, _ = _correr(session, tmp_path, waha, config=sin_clave)

        assert waha.envios == []
        assert reporte.exit_code == 1

    def test_no_publica_con_la_sesion_de_ingesta(self, session: Session, tmp_path: Path) -> None:
        """Número aparte (docs/decisions.md 2026-10-20): un baneo del número que
        publica no puede frenar la ingesta. Si la configuración apunta las dos
        a la misma sesión, no se manda nada."""
        _post(session, tmp_path)
        waha = Waha()
        misma = ChannelConfig(**{**CONFIG.__dict__, "ingest_session": "guani-canal"})

        reporte, _ = _correr(session, tmp_path, waha, config=misma)

        assert waha.envios == []
        assert reporte.exit_code == 1
        assert "SAIMC_WAHA_CHANNEL_SESSION" in reporte.message

    def test_si_falta_el_jpeg_no_manda_esa_lamina_y_sale_con_1(
        self, session: Session, tmp_path: Path
    ) -> None:
        post = _post(session, tmp_path)
        (tmp_path / str(post.id) / "2.jpg").unlink()
        waha = Waha()

        reporte, _ = _correr(session, tmp_path, waha)

        assert reporte.exit_code == 1
        assert len(waha.envios) == 1
        session.refresh(post)
        assert post.channel_slides_sent == 1


# --- CLI ------------------------------------------------------------------------------


def test_el_cli_corre_en_seco_por_defecto(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _post(session, tmp_path / "posts")

    @contextmanager
    def _scope():
        yield session

    def _sin_red(*a: object, **k: object) -> None:
        raise AssertionError("en seco no se llama a WAHA")

    monkeypatch.setattr("saimc.marketing.channel.session_scope", _scope)
    monkeypatch.setattr("saimc.marketing.channel.default_marketing_dir", lambda: tmp_path)
    monkeypatch.setattr("saimc.marketing.channel.channel_config", lambda: CONFIG)
    monkeypatch.setattr("saimc.marketing.channel.send_image", _sin_red)

    codigo = main(["--slot", "datos", "--day", "2026-10-20"])

    assert codigo == 0
    salida = capsys.readouterr().out
    assert "EN SECO" in salida
    assert "3 láminas" in salida


def test_el_cli_solo_acepta_los_dos_turnos() -> None:
    with pytest.raises(SystemExit):
        main(["--slot", "dato-del-dia"])


# --- migración 0034 --------------------------------------------------------------------


def _cargar_migracion(nombre: str):
    ruta = Path(__file__).resolve().parent.parent / "alembic" / "versions" / nombre
    spec = importlib.util.spec_from_file_location(nombre.removesuffix(".py"), ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_la_migracion_0034_agrega_el_registro_del_canal_y_lo_quita() -> None:
    from saimc.models import Base

    base = _cargar_migracion("0033_marketing_posts.py")
    modulo = _cargar_migracion("0034_marketing_channel_send.py")
    assert (modulo.revision, modulo.down_revision) == ("0034", "0033")
    engine = create_engine("sqlite://")

    with engine.connect() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            base.upgrade()
        # Una publicación de antes de la migración: tiene que quedar «nada enviado».
        conn.execute(
            sa.text(
                "INSERT INTO marketing_posts (id, format, status, planned_for, origin,"
                " batch_slot, created_at) VALUES ('a', 'single', 'draft', '2026-10-19',"
                " 'batch', 'datos', '2026-10-19')"
            )
        )
        with Operations.context(MigrationContext.configure(conn)):
            modulo.upgrade()
        conn.commit()
        migradas = {c["name"] for c in inspect(conn).get_columns("marketing_posts")}
        declaradas = {c.name for c in Base.metadata.tables["marketing_posts"].columns}
        assert migradas == declaradas
        fila = conn.execute(sa.text("SELECT channel_slides_sent, channel_sent_at FROM marketing_posts")).one()
        assert tuple(fila) == (0, None)

        with Operations.context(MigrationContext.configure(conn)):
            modulo.downgrade()
        conn.commit()
        restantes = {c["name"] for c in inspect(conn).get_columns("marketing_posts")}
        assert {"channel_slides_sent", "channel_sent_at"}.isdisjoint(restantes)
    engine.dispose()
