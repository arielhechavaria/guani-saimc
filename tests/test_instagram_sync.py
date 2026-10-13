"""El lote de Instagram (`saimc.instagram.sync`), las tablas y su migración.

Sin red: la API se simula con `httpx.MockTransport` y cuerpos escritos a mano
(los de `test_instagram_client.py`). La base es la SQLite en memoria de
`conftest.py`. Los casos fijan el comportamiento pedido en
`docs/spec-slice-instagram.md`: en seco no escribe, las métricas solo se
agregan, el token se siembra desde el `.env` una vez y se renueva solo con más
de 24 h de vida y menos de 15 días por delante, un token vencido sale con un
código distinto de 0, y sin token se degrada sin fallar.
"""

# Tokens de mentira escritos a mano: no son secretos.
# ruff: noqa: S105, S106
from __future__ import annotations

import importlib.util
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import httpx
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from saimc.config import Settings
from saimc.instagram import sync
from saimc.models import (
    InstagramAccountSnapshot,
    InstagramCredentials,
    InstagramMedia,
    InstagramMediaMetrics,
)
from tests.test_instagram_client import (
    CUENTA,
    INSIGHTS_REEL,
    INSIGHTS_STORY,
    METRICA_INVALIDA,
    PUBLICACIONES,
    RENOVACION,
    STORIES,
    TOKEN_VENCIDO,
)

AHORA = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
TOKEN_ENV = "IGAAtokendelalta"

# Insights de un post del feed (el primero de PUBLICACIONES).
INSIGHTS_FEED = json.dumps(
    {
        "data": [
            {"name": "views", "values": [{"value": 400}]},
            {"name": "reach", "values": [{"value": 300}]},
            {"name": "likes", "values": [{"value": 20}]},
            {"name": "comments", "values": [{"value": 2}]},
            {"name": "saved", "values": [{"value": 4}]},
            {"name": "shares", "values": [{"value": 1}]},
            {"name": "total_interactions", "values": [{"value": 27}]},
        ]
    }
)


class FakeGraph:
    """La API simulada: responde por ruta y anota cada pedido con su token."""

    def __init__(self, **overrides: tuple[int, str]) -> None:
        self.routes: dict[str, tuple[int, str]] = {
            "/v25.0/me": (200, CUENTA),
            "/v25.0/me/media": (200, PUBLICACIONES),
            "/v25.0/me/stories": (200, STORIES),
            "/v25.0/18000000000000001/insights": (200, INSIGHTS_FEED),
            "/v25.0/18000000000000002/insights": (200, INSIGHTS_REEL),
            "/v25.0/18000000000000009/insights": (200, INSIGHTS_STORY),
            "/refresh_access_token": (200, RENOVACION),
        }
        self.routes.update({k.replace("__", "/"): v for k, v in overrides.items()})
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            status, body = self.routes.get(request.url.path, (404, "{}"))
            return httpx.Response(status, text=body)

        return httpx.MockTransport(handle)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]

    def tokens(self) -> set[str]:
        return {r.url.params["access_token"] for r in self.requests}


def _run(
    session: Session,
    graph: FakeGraph,
    *,
    execute: bool = True,
    env_token: str | None = TOKEN_ENV,
    now: datetime = AHORA,
) -> sync.SyncReport:
    report = sync.run_sync(
        session, env_token=env_token, now=now, execute=execute, transport=graph.transport()
    )
    session.commit()
    return report


def _credenciales(session: Session, *, token: str, refreshed: datetime, expires: datetime) -> None:
    session.add(InstagramCredentials(access_token=token, refreshed_at=refreshed, expires_at=expires))
    session.commit()


def _cuenta(session: Session, modelo: type) -> int:
    return session.scalar(select(func.count()).select_from(modelo)) or 0


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


# --- Cuándo renovar -------------------------------------------------------------


class TestNeedsRefresh:
    @pytest.mark.parametrize(
        ("edad", "faltan", "esperado"),
        [
            (timedelta(days=10), timedelta(days=50), False),  # lejos del vencimiento
            (timedelta(days=46), timedelta(days=14), True),  # dentro de los 15 días
            (timedelta(hours=12), timedelta(days=10), False),  # menos de 24 h: Meta no renueva
            (timedelta(hours=25), timedelta(days=14, hours=23), True),
            (timedelta(days=45), timedelta(days=15), False),  # justo 15 días: todavía no
        ],
    )
    def test_solo_con_mas_de_24_h_y_menos_de_15_dias(
        self, edad: timedelta, faltan: timedelta, esperado: bool
    ) -> None:
        assert sync.needs_refresh(refreshed_at=AHORA - edad, expires_at=AHORA + faltan, now=AHORA) is esperado


# --- Sin token ------------------------------------------------------------------


class TestSinToken:
    def test_sin_fila_ni_env_no_llama_no_escribe_y_sale_con_0(self, session: Session) -> None:
        graph = FakeGraph()

        report = _run(session, graph, env_token=None)

        assert report.exit_code == 0
        assert report.fatal is None
        assert "SAIMC_INSTAGRAM_ACCESS_TOKEN" in report.credential_note
        assert graph.requests == []
        assert _cuenta(session, InstagramCredentials) == 0


# --- Corrida real ---------------------------------------------------------------


class TestPrimeraCorrida:
    def test_siembra_el_token_del_env_con_60_dias(self, session: Session) -> None:
        _run(session, FakeGraph())

        cred = session.get(InstagramCredentials, 1)
        assert cred is not None
        assert cred.access_token == TOKEN_ENV
        assert _as_utc(cred.refreshed_at) == AHORA
        assert _as_utc(cred.expires_at) == AHORA + timedelta(days=60)

    def test_un_token_recien_sembrado_no_se_renueva(self, session: Session) -> None:
        graph = FakeGraph()

        _run(session, graph)

        assert "/refresh_access_token" not in graph.paths()
        assert graph.tokens() == {TOKEN_ENV}

    def test_guarda_seguidores_publicaciones_y_metricas(self, session: Session) -> None:
        report = _run(session, FakeGraph())

        assert report.exit_code == 0
        (foto,) = session.scalars(select(InstagramAccountSnapshot)).all()
        assert (foto.followers_count, foto.media_count) == (312, 27)
        tipos = {m.ig_media_id: m.media_type for m in session.scalars(select(InstagramMedia))}
        assert tipos == {
            "18000000000000001": "FEED",
            "18000000000000002": "REELS",
            "18000000000000003": "FEED",
            "18000000000000009": "STORY",
        }
        # Carrusel sin métricas: 3 filas, no 4.
        assert _cuenta(session, InstagramMediaMetrics) == 3
        assert report.media_new == 4
        assert report.metrics_rows == 3

    def test_las_metricas_del_reel_traen_el_tiempo_medio(self, session: Session) -> None:
        _run(session, FakeGraph())

        reel = session.scalars(
            select(InstagramMedia).where(InstagramMedia.ig_media_id == "18000000000000002")
        ).one()
        fila = session.scalars(
            select(InstagramMediaMetrics).where(InstagramMediaMetrics.media_id == reel.id)
        ).one()
        assert (fila.views, fila.reach, fila.total_interactions, fila.avg_watch_time_ms) == (
            1520, 980, 92, 8450,
        )
        assert _as_utc(fila.captured_at) == AHORA

    def test_al_carrusel_no_se_le_piden_insights(self, session: Session) -> None:
        graph = FakeGraph()

        report = _run(session, graph)

        assert "/v25.0/18000000000000003/insights" not in graph.paths()
        assert any("carrusel" in s for s in report.skipped)


class TestSegundaCorrida:
    def test_agrega_una_lectura_nueva_sin_tocar_la_anterior(self, session: Session) -> None:
        _run(session, FakeGraph())
        primera = {
            (f.media_id, _as_utc(f.captured_at), f.views)
            for f in session.scalars(select(InstagramMediaMetrics))
        }

        cambio = json.loads(PUBLICACIONES)
        cambio["data"][0]["caption"] = "El arroz esta semana en La Habana (corregido)"
        luego = AHORA + timedelta(hours=3)
        _run(session, FakeGraph(**{"/v25.0/me/media": (200, json.dumps(cambio))}), now=luego)

        filas = {
            (f.media_id, _as_utc(f.captured_at), f.views)
            for f in session.scalars(select(InstagramMediaMetrics))
        }
        assert primera < filas
        assert len(filas) == 6
        assert _cuenta(session, InstagramMedia) == 4
        assert _cuenta(session, InstagramAccountSnapshot) == 2
        post = session.scalars(
            select(InstagramMedia).where(InstagramMedia.ig_media_id == "18000000000000001")
        ).one()
        assert post.caption is not None and post.caption.endswith("(corregido)")
        assert _as_utc(post.first_seen_at) == AHORA
        assert _as_utc(post.last_synced_at) == luego

    def test_una_story_de_mas_de_24_h_no_pide_metricas(self, session: Session) -> None:
        graph = FakeGraph()

        report = _run(session, graph, now=datetime(2026, 10, 14, 9, 0, tzinfo=UTC))

        assert "/v25.0/18000000000000009/insights" not in graph.paths()
        assert any("story" in s for s in report.skipped)


# --- En seco --------------------------------------------------------------------


class TestEnSeco:
    def test_lee_pero_no_escribe_nada(self, session: Session) -> None:
        graph = FakeGraph()

        report = _run(session, graph, execute=False)

        assert report.exit_code == 0
        assert report.media_new == 4
        assert report.metrics_rows == 3
        for modelo in (
            InstagramCredentials,
            InstagramAccountSnapshot,
            InstagramMedia,
            InstagramMediaMetrics,
        ):
            assert _cuenta(session, modelo) == 0

    def test_no_renueva_aunque_toque(self, session: Session) -> None:
        _credenciales(
            session,
            token="IGAAviejo",
            refreshed=AHORA - timedelta(days=50),
            expires=AHORA + timedelta(days=10),
        )
        graph = FakeGraph()

        report = _run(session, graph, execute=False)

        assert "/refresh_access_token" not in graph.paths()
        assert "renovaría" in report.credential_note
        assert session.get(InstagramCredentials, 1).access_token == "IGAAviejo"  # type: ignore[union-attr]


# --- Renovación -----------------------------------------------------------------


class TestRenovacion:
    def test_renueva_guarda_el_token_nuevo_y_lee_con_el(self, session: Session) -> None:
        _credenciales(
            session,
            token="IGAAviejo",
            refreshed=AHORA - timedelta(days=50),
            expires=AHORA + timedelta(days=10),
        )
        graph = FakeGraph()

        report = _run(session, graph)

        cred = session.get(InstagramCredentials, 1)
        assert cred is not None
        assert cred.access_token == "IGAAtokennuevo"
        assert _as_utc(cred.expires_at) == AHORA + timedelta(seconds=5183944)
        assert _as_utc(cred.refreshed_at) == AHORA
        assert graph.paths()[0] == "/refresh_access_token"
        lecturas = {r.url.params["access_token"] for r in graph.requests[1:]}
        assert lecturas == {"IGAAtokennuevo"}
        assert report.exit_code == 0

    def test_la_fila_de_la_base_gana_sobre_el_env(self, session: Session) -> None:
        _credenciales(
            session,
            token="IGAAdelabase",
            refreshed=AHORA - timedelta(days=2),
            expires=AHORA + timedelta(days=58),
        )
        graph = FakeGraph()

        _run(session, graph, env_token="IGAAotrodelenv")

        assert graph.tokens() == {"IGAAdelabase"}

    def test_si_la_renovacion_falla_por_red_sigue_con_el_token_viejo(self, session: Session) -> None:
        _credenciales(
            session,
            token="IGAAviejo",
            refreshed=AHORA - timedelta(days=50),
            expires=AHORA + timedelta(days=10),
        )
        graph = FakeGraph(**{"/refresh_access_token": (500, "<html>error</html>")})

        report = _run(session, graph)

        assert report.exit_code == 0
        assert report.errors
        assert session.get(InstagramCredentials, 1).access_token == "IGAAviejo"  # type: ignore[union-attr]
        assert _cuenta(session, InstagramAccountSnapshot) == 1


# --- Token vencido --------------------------------------------------------------


class TestTokenVencido:
    def test_vencido_por_fecha_no_llama_y_sale_distinto_de_0(self, session: Session) -> None:
        _credenciales(
            session,
            token="IGAAviejo",
            refreshed=AHORA - timedelta(days=61),
            expires=AHORA - timedelta(days=1),
        )
        graph = FakeGraph()

        report = _run(session, graph)

        assert report.exit_code != 0
        assert report.fatal is not None
        assert "venció" in report.fatal
        assert "a mano" in report.fatal
        assert graph.requests == []

    def test_meta_responde_190_al_leer_la_cuenta(self, session: Session) -> None:
        graph = FakeGraph(**{"/v25.0/me": (400, TOKEN_VENCIDO)})

        report = _run(session, graph)

        assert report.exit_code != 0
        assert report.fatal is not None and "a mano" in report.fatal
        assert _cuenta(session, InstagramAccountSnapshot) == 0
        assert _cuenta(session, InstagramMedia) == 0

    def test_meta_responde_190_al_renovar(self, session: Session) -> None:
        _credenciales(
            session,
            token="IGAAviejo",
            refreshed=AHORA - timedelta(days=50),
            expires=AHORA + timedelta(days=10),
        )
        graph = FakeGraph(**{"/refresh_access_token": (400, TOKEN_VENCIDO)})

        report = _run(session, graph)

        assert report.exit_code != 0
        assert graph.paths() == ["/refresh_access_token"]

    def test_el_mensaje_nunca_lleva_el_token(self, session: Session) -> None:
        graph = FakeGraph(**{"/v25.0/me": (400, TOKEN_VENCIDO)})

        report = _run(session, graph)

        assert TOKEN_ENV not in sync.format_report(report, execute=True)


# --- Errores parciales ----------------------------------------------------------


class TestErroresParciales:
    def test_una_metrica_rechazada_no_frena_al_resto(self, session: Session) -> None:
        graph = FakeGraph(**{"/v25.0/18000000000000001/insights": (400, METRICA_INVALIDA)})

        report = _run(session, graph)

        assert report.exit_code == 0
        assert _cuenta(session, InstagramMediaMetrics) == 2
        assert any("18000000000000001" in e for e in report.errors)

    def test_sin_stories_se_sigue_con_el_feed(self, session: Session) -> None:
        graph = FakeGraph(**{"/v25.0/me/stories": (500, "{}")})

        report = _run(session, graph)

        assert report.exit_code == 0
        assert _cuenta(session, InstagramMedia) == 3
        assert report.errors

    def test_sin_red_sale_distinto_de_0_y_no_escribe(self, session: Session) -> None:
        def caida(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("sin red", request=request)

        report = sync.run_sync(
            session, env_token=TOKEN_ENV, now=AHORA, execute=True, transport=httpx.MockTransport(caida)
        )

        assert report.exit_code != 0
        assert _cuenta(session, InstagramAccountSnapshot) == 0


# --- main -----------------------------------------------------------------------


class TestMain:
    @pytest.fixture
    def entorno(self, session: Session, monkeypatch: pytest.MonkeyPatch) -> FakeGraph:
        graph = FakeGraph()

        @contextmanager
        def _scope() -> Iterator[Session]:
            yield session
            session.commit()

        monkeypatch.setattr(sync, "session_scope", _scope)
        monkeypatch.setattr(sync, "_default_transport", graph.transport)
        return graph

    def test_en_seco_por_defecto(
        self, entorno: FakeGraph, session: Session, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            sync, "get_settings", lambda: Settings(_env_file=None, instagram_access_token=TOKEN_ENV)  # type: ignore[call-arg]
        )

        codigo = sync.main([])

        assert codigo == 0
        assert _cuenta(session, InstagramMedia) == 0
        assert "--execute" in capsys.readouterr().out

    def test_execute_escribe(
        self, entorno: FakeGraph, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            sync, "get_settings", lambda: Settings(_env_file=None, instagram_access_token=TOKEN_ENV)  # type: ignore[call-arg]
        )

        assert sync.main(["--execute"]) == 0
        assert _cuenta(session, InstagramMedia) == 4

    def test_sin_token_sale_con_0(
        self, entorno: FakeGraph, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(sync, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]

        assert sync.main(["--execute"]) == 0
        assert "SAIMC_INSTAGRAM_ACCESS_TOKEN" in capsys.readouterr().out
        assert entorno.requests == []


# --- Tablas y migración ---------------------------------------------------------


class TestTablas:
    def test_las_credenciales_son_una_sola_fila(self, session: Session) -> None:
        session.add(
            InstagramCredentials(
                id=2, access_token="x", refreshed_at=AHORA, expires_at=AHORA + timedelta(days=60)
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    def test_una_lectura_por_publicacion_y_momento(self, session: Session) -> None:
        media = InstagramMedia(ig_media_id="1", media_type="FEED")
        session.add(media)
        session.flush()
        session.add(InstagramMediaMetrics(media_id=media.id, captured_at=AHORA, views=1))
        session.add(InstagramMediaMetrics(media_id=media.id, captured_at=AHORA, views=2))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_el_tipo_tiene_check(self, session: Session) -> None:
        session.add(InstagramMedia(ig_media_id="1", media_type="CAROUSEL"))
        with pytest.raises(IntegrityError):
            session.commit()


def _cargar_migracion(nombre: str) -> ModuleType:
    ruta = Path(__file__).resolve().parent.parent / "alembic" / "versions" / nombre
    spec = importlib.util.spec_from_file_location(nombre.removesuffix(".py"), ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


class TestMigracion0032:
    _TABLAS = (
        "instagram_media",
        "instagram_media_metrics",
        "instagram_account_snapshots",
        "instagram_credentials",
    )

    def test_sube_con_las_columnas_del_modelo_y_baja(self) -> None:
        from saimc.models import Base

        modulo = _cargar_migracion("0032_instagram.py")
        assert (modulo.revision, modulo.down_revision) == ("0032", "0031")
        engine = create_engine("sqlite://")

        with engine.connect() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                modulo.upgrade()
            conn.commit()
            insp = inspect(conn)
            for tabla in self._TABLAS:
                migradas = {c["name"] for c in insp.get_columns(tabla)}
                declaradas = {c.name for c in Base.metadata.tables[tabla].columns}
                assert migradas == declaradas, tabla
            unicas = {
                u["name"]: tuple(u["column_names"])
                for u in insp.get_unique_constraints("instagram_media_metrics")
            }
            assert unicas == {"uq_instagram_media_metrics_media_captured": ("media_id", "captured_at")}

            conn.execute(
                sa.text(
                    "INSERT INTO instagram_credentials (id, access_token, expires_at, refreshed_at) "
                    "VALUES (1, 't', '2026-12-12', '2026-10-13')"
                )
            )
            with pytest.raises(IntegrityError):
                conn.execute(
                    sa.text(
                        "INSERT INTO instagram_credentials (id, access_token, expires_at, refreshed_at) "
                        "VALUES (2, 't', '2026-12-12', '2026-10-13')"
                    )
                )
            conn.rollback()

        with engine.connect() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                modulo.downgrade()
            conn.commit()
            restantes = set(inspect(conn).get_table_names())
            assert restantes.isdisjoint(self._TABLAS)
        engine.dispose()
