"""Cliente de la API de Instagram (`saimc.instagram.client`), sin red.

Los cuerpos de respuesta están escritos a mano con la forma que documenta Meta
para *Instagram API with Instagram Login* (graph.instagram.com, consultada el
2026-10-13), y la respuesta esperada de cada caso se fijó antes de escribir el
parseo. El transporte se prueba con `httpx.MockTransport`: ninguna llamada sale
a la red.
"""

# Tokens de mentira escritos a mano: no son secretos.
# ruff: noqa: S105, S106
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from saimc.instagram.client import (
    Account,
    GraphClient,
    GraphError,
    MediaItem,
    Metrics,
    RefreshedToken,
    metrics_for,
    parse_account,
    parse_error,
    parse_insights,
    parse_media_list,
    parse_refresh,
    refresh_token,
)
from saimc.models import InstagramMediaType

AHORA = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
TOKEN = "IGAAtokendeprueba"

# --- Cuerpos de la Graph API, escritos a mano -----------------------------------

CUENTA = json.dumps(
    {
        "user_id": "17841400000000001",
        "username": "guani.cuba",
        "followers_count": 312,
        "media_count": 27,
        "id": "9000000000000001",
    }
)

PUBLICACIONES = json.dumps(
    {
        "data": [
            {
                "id": "18000000000000001",
                "media_type": "IMAGE",
                "media_product_type": "FEED",
                "permalink": "https://www.instagram.com/p/AAA111/",
                "caption": "El arroz esta semana en La Habana",
                "timestamp": "2026-10-10T14:05:00+0000",
            },
            {
                "id": "18000000000000002",
                "media_type": "VIDEO",
                "media_product_type": "REELS",
                "permalink": "https://www.instagram.com/reel/BBB222/",
                "caption": "De los grupos a la cifra",
                "timestamp": "2026-10-11T20:30:00+0000",
            },
            {
                "id": "18000000000000003",
                "media_type": "CAROUSEL_ALBUM",
                "media_product_type": "FEED",
                "permalink": "https://www.instagram.com/p/CCC333/",
                "caption": "Cómo leer los precios",
                "timestamp": "2026-10-12T09:00:00+0000",
            },
        ],
        "paging": {"cursors": {"before": "QVFI", "after": "QVFJ"}},
    }
)

STORIES = json.dumps(
    {
        "data": [
            {
                "id": "18000000000000009",
                "media_type": "IMAGE",
                "permalink": "https://www.instagram.com/stories/guani.cuba/18000000000000009/",
                "timestamp": "2026-10-13T08:00:00+0000",
            }
        ]
    }
)

INSIGHTS_REEL = json.dumps(
    {
        "data": [
            {"name": "views", "period": "lifetime", "values": [{"value": 1520}],
             "title": "Views", "id": "18000000000000002/insights/views/lifetime"},
            {"name": "reach", "period": "lifetime", "values": [{"value": 980}],
             "title": "Accounts reached", "id": "18000000000000002/insights/reach/lifetime"},
            {"name": "likes", "period": "lifetime", "values": [{"value": 64}],
             "title": "Likes", "id": "18000000000000002/insights/likes/lifetime"},
            {"name": "comments", "period": "lifetime", "values": [{"value": 7}],
             "title": "Comments", "id": "18000000000000002/insights/comments/lifetime"},
            {"name": "saved", "period": "lifetime", "values": [{"value": 12}],
             "title": "Saved", "id": "18000000000000002/insights/saved/lifetime"},
            {"name": "shares", "period": "lifetime", "values": [{"value": 9}],
             "title": "Shares", "id": "18000000000000002/insights/shares/lifetime"},
            {"name": "total_interactions", "period": "lifetime", "values": [{"value": 92}],
             "title": "Interactions", "id": "18000000000000002/insights/total_interactions/lifetime"},
            {"name": "ig_reels_avg_watch_time", "period": "lifetime", "values": [{"value": 8450}],
             "title": "Average watch time",
             "id": "18000000000000002/insights/ig_reels_avg_watch_time/lifetime"},
        ]
    }
)

# Forma con `total_value` en lugar de `values`, que Meta usa en las métricas
# nuevas. Una story: sin likes, comentarios ni guardados.
INSIGHTS_STORY = json.dumps(
    {
        "data": [
            {"name": "views", "period": "lifetime", "total_value": {"value": 210},
             "id": "18000000000000009/insights/views/lifetime"},
            {"name": "reach", "period": "lifetime", "total_value": {"value": 150},
             "id": "18000000000000009/insights/reach/lifetime"},
            {"name": "shares", "period": "lifetime", "total_value": {"value": 2},
             "id": "18000000000000009/insights/shares/lifetime"},
            {"name": "total_interactions", "period": "lifetime", "total_value": {"value": 5},
             "id": "18000000000000009/insights/total_interactions/lifetime"},
        ]
    }
)

RENOVACION = json.dumps(
    {"access_token": "IGAAtokennuevo", "token_type": "bearer", "expires_in": 5183944}
)

TOKEN_VENCIDO = json.dumps(
    {
        "error": {
            "message": "Error validating access token: Session has expired on Monday, "
            "12-Oct-26 10:00:00 PDT.",
            "type": "OAuthException",
            "code": 190,
            "error_subcode": 463,
            "fbtrace_id": "AbCdEfGh",
        }
    }
)

METRICA_INVALIDA = json.dumps(
    {
        "error": {
            "message": "(#100) Incompatible metric for this media type",
            "type": "OAuthException",
            "code": 100,
            "fbtrace_id": "XyZ",
        }
    }
)


# --- Parseo puro ----------------------------------------------------------------


class TestParseAccount:
    def test_lee_seguidores_y_cantidad_de_publicaciones(self) -> None:
        assert parse_account(CUENTA) == Account(followers_count=312, media_count=27)

    @pytest.mark.parametrize(
        "cuerpo",
        [
            "no es json",
            "[]",
            json.dumps({"username": "guani.cuba", "media_count": 27}),
            json.dumps({"followers_count": "312", "media_count": 27}),
            json.dumps({"followers_count": True, "media_count": 27}),
            json.dumps({"followers_count": -1, "media_count": 27}),
            TOKEN_VENCIDO,
        ],
    )
    def test_cualquier_otra_forma_degrada_a_none(self, cuerpo: str) -> None:
        assert parse_account(cuerpo) is None


class TestParseMediaList:
    def test_clasifica_feed_reel_y_carrusel_con_su_fecha(self) -> None:
        items = parse_media_list(PUBLICACIONES)

        assert items == [
            MediaItem(
                ig_media_id="18000000000000001",
                media_type=InstagramMediaType.FEED,
                is_carousel=False,
                permalink="https://www.instagram.com/p/AAA111/",
                caption="El arroz esta semana en La Habana",
                posted_at=datetime(2026, 10, 10, 14, 5, tzinfo=UTC),
            ),
            MediaItem(
                ig_media_id="18000000000000002",
                media_type=InstagramMediaType.REELS,
                is_carousel=False,
                permalink="https://www.instagram.com/reel/BBB222/",
                caption="De los grupos a la cifra",
                posted_at=datetime(2026, 10, 11, 20, 30, tzinfo=UTC),
            ),
            MediaItem(
                ig_media_id="18000000000000003",
                media_type=InstagramMediaType.FEED,
                is_carousel=True,
                permalink="https://www.instagram.com/p/CCC333/",
                caption="Cómo leer los precios",
                posted_at=datetime(2026, 10, 12, 9, 0, tzinfo=UTC),
            ),
        ]

    def test_sin_media_product_type_un_video_es_reel_y_una_imagen_es_feed(self) -> None:
        cuerpo = json.dumps(
            {
                "data": [
                    {"id": "1", "media_type": "VIDEO", "timestamp": "2026-10-01T00:00:00+0000"},
                    {"id": "2", "media_type": "IMAGE", "timestamp": "2026-10-01T00:00:00+0000"},
                ]
            }
        )

        tipos = [item.media_type for item in parse_media_list(cuerpo)]

        assert tipos == [InstagramMediaType.REELS, InstagramMediaType.FEED]

    def test_lo_que_viene_de_stories_es_story(self) -> None:
        (story,) = parse_media_list(STORIES, stories=True)

        assert story.media_type == InstagramMediaType.STORY
        assert story.caption is None
        assert story.posted_at == datetime(2026, 10, 13, 8, 0, tzinfo=UTC)

    def test_un_item_sin_id_se_saltea_y_una_fecha_rota_queda_nula(self) -> None:
        cuerpo = json.dumps(
            {
                "data": [
                    {"media_type": "IMAGE", "timestamp": "2026-10-01T00:00:00+0000"},
                    {"id": "7", "media_type": "IMAGE", "timestamp": "ayer"},
                    "basura",
                ]
            }
        )

        items = parse_media_list(cuerpo)

        assert [i.ig_media_id for i in items] == ["7"]
        assert items[0].posted_at is None

    @pytest.mark.parametrize("cuerpo", ["no es json", "{}", json.dumps({"data": "x"}), TOKEN_VENCIDO])
    def test_cuerpo_inesperado_da_lista_vacia(self, cuerpo: str) -> None:
        assert parse_media_list(cuerpo) == []


class TestParseInsights:
    def test_reel_con_todas_las_metricas_y_el_tiempo_medio(self) -> None:
        assert parse_insights(INSIGHTS_REEL) == Metrics(
            views=1520,
            reach=980,
            likes=64,
            comments=7,
            saved=12,
            shares=9,
            total_interactions=92,
            avg_watch_time_ms=8450,
        )

    def test_story_con_total_value_y_lo_que_falta_queda_nulo(self) -> None:
        assert parse_insights(INSIGHTS_STORY) == Metrics(
            views=210,
            reach=150,
            likes=None,
            comments=None,
            saved=None,
            shares=2,
            total_interactions=5,
            avg_watch_time_ms=None,
        )

    def test_impressions_no_se_lee(self) -> None:
        cuerpo = json.dumps(
            {"data": [{"name": "impressions", "values": [{"value": 99}]},
                      {"name": "views", "values": [{"value": 10}]}]}
        )

        metricas = parse_insights(cuerpo)

        assert metricas is not None
        assert metricas.views == 10

    def test_un_valor_que_no_es_entero_queda_nulo(self) -> None:
        cuerpo = json.dumps(
            {"data": [{"name": "views", "values": [{"value": "mucho"}]},
                      {"name": "reach", "values": []},
                      {"name": "likes", "values": [{"value": 3}]}]}
        )

        metricas = parse_insights(cuerpo)

        assert metricas is not None
        assert (metricas.views, metricas.reach, metricas.likes) == (None, None, 3)

    @pytest.mark.parametrize(
        "cuerpo",
        ["no es json", "{}", json.dumps({"data": []}), json.dumps({"data": [{"name": "x"}]}),
         METRICA_INVALIDA],
    )
    def test_sin_ninguna_metrica_conocida_da_none(self, cuerpo: str) -> None:
        assert parse_insights(cuerpo) is None


class TestParseRefresh:
    def test_el_token_nuevo_vence_expires_in_segundos_despues(self) -> None:
        assert parse_refresh(RENOVACION, now=AHORA) == RefreshedToken(
            access_token="IGAAtokennuevo",
            expires_at=AHORA + timedelta(seconds=5183944),
        )

    @pytest.mark.parametrize(
        "cuerpo",
        [
            "no es json",
            json.dumps({"token_type": "bearer", "expires_in": 5183944}),
            json.dumps({"access_token": "", "expires_in": 5183944}),
            json.dumps({"access_token": "IGAAx", "expires_in": "5183944"}),
            json.dumps({"access_token": "IGAAx", "expires_in": 0}),
            TOKEN_VENCIDO,
        ],
    )
    def test_cualquier_otra_forma_degrada_a_none(self, cuerpo: str) -> None:
        assert parse_refresh(cuerpo, now=AHORA) is None


class TestParseError:
    def test_el_codigo_190_es_token_invalido(self) -> None:
        error = parse_error(TOKEN_VENCIDO, status=400)

        assert error.code == 190
        assert error.token_invalid
        assert "Session has expired" in error.message

    def test_otro_codigo_no_es_token_invalido(self) -> None:
        error = parse_error(METRICA_INVALIDA, status=400)

        assert error.code == 100
        assert not error.token_invalid

    def test_un_cuerpo_sin_forma_de_error_da_un_mensaje_con_el_status(self) -> None:
        error = parse_error("<html>Bad gateway</html>", status=502)

        assert error.code is None
        assert not error.token_invalid
        assert "502" in error.message


class TestMetricsFor:
    def test_feed_pide_views_y_no_impressions(self) -> None:
        item = MediaItem("1", InstagramMediaType.FEED, False, None, None, None)

        assert metrics_for(item) == (
            "views", "reach", "likes", "comments", "saved", "shares", "total_interactions",
        )

    def test_reel_suma_el_tiempo_medio_de_reproduccion(self) -> None:
        item = MediaItem("1", InstagramMediaType.REELS, False, None, None, None)

        metricas = metrics_for(item)

        assert metricas is not None
        assert metricas[-1] == "ig_reels_avg_watch_time"
        assert "impressions" not in metricas

    def test_story_no_pide_likes_comentarios_ni_guardados(self) -> None:
        item = MediaItem("1", InstagramMediaType.STORY, False, None, None, None)

        assert metrics_for(item) == ("views", "reach", "shares", "total_interactions")

    def test_un_carrusel_no_tiene_insights(self) -> None:
        item = MediaItem("1", InstagramMediaType.FEED, True, None, None, None)

        assert metrics_for(item) is None


# --- Transporte -----------------------------------------------------------------


def _transporte(respuestas: dict[str, tuple[int, str]], pedidos: list[httpx.Request]) -> httpx.MockTransport:
    """Responde por ruta (`/v25.0/me`, `/refresh_access_token`...) y anota cada pedido."""

    def manejar(request: httpx.Request) -> httpx.Response:
        pedidos.append(request)
        status, cuerpo = respuestas.get(request.url.path, (404, "{}"))
        return httpx.Response(status, text=cuerpo)

    return httpx.MockTransport(manejar)


class TestGraphClient:
    def test_sin_token_no_llama_y_devuelve_un_error(self) -> None:
        pedidos: list[httpx.Request] = []
        cliente = GraphClient("", transport=_transporte({}, pedidos))

        resultado = cliente.account()

        assert isinstance(resultado, GraphError)
        assert pedidos == []

    def test_account_pide_los_campos_de_la_cuenta_propia(self) -> None:
        pedidos: list[httpx.Request] = []
        cliente = GraphClient(TOKEN, transport=_transporte({"/v25.0/me": (200, CUENTA)}, pedidos))

        assert cliente.account() == Account(followers_count=312, media_count=27)
        (pedido,) = pedidos
        assert pedido.url.host == "graph.instagram.com"
        assert pedido.url.params["access_token"] == TOKEN
        assert set(pedido.url.params["fields"].split(",")) >= {"followers_count", "media_count"}

    def test_media_y_stories_piden_sus_rutas(self) -> None:
        pedidos: list[httpx.Request] = []
        cliente = GraphClient(
            TOKEN,
            transport=_transporte(
                {"/v25.0/me/media": (200, PUBLICACIONES), "/v25.0/me/stories": (200, STORIES)},
                pedidos,
            ),
        )

        media = cliente.media()
        stories = cliente.stories()

        assert isinstance(media, list) and len(media) == 3
        assert isinstance(stories, list) and stories[0].media_type == InstagramMediaType.STORY
        assert "timestamp" in pedidos[0].url.params["fields"]

    def test_insights_pide_exactamente_las_metricas_dadas(self) -> None:
        pedidos: list[httpx.Request] = []
        cliente = GraphClient(
            TOKEN,
            transport=_transporte(
                {"/v25.0/18000000000000009/insights": (200, INSIGHTS_STORY)}, pedidos
            ),
        )

        resultado = cliente.insights("18000000000000009", ("views", "reach", "shares", "total_interactions"))

        assert isinstance(resultado, Metrics)
        assert pedidos[0].url.params["metric"] == "views,reach,shares,total_interactions"

    def test_token_vencido_vuelve_como_error_190(self) -> None:
        pedidos: list[httpx.Request] = []
        cliente = GraphClient(TOKEN, transport=_transporte({"/v25.0/me": (400, TOKEN_VENCIDO)}, pedidos))

        resultado = cliente.account()

        assert isinstance(resultado, GraphError)
        assert resultado.token_invalid

    def test_un_200_con_cuerpo_ilegible_es_un_error(self) -> None:
        pedidos: list[httpx.Request] = []
        cliente = GraphClient(TOKEN, transport=_transporte({"/v25.0/me": (200, "<html>")}, pedidos))

        assert isinstance(cliente.account(), GraphError)

    def test_sin_red_no_levanta_y_el_mensaje_no_lleva_el_token(self) -> None:
        def caida(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"no se pudo conectar a {request.url}", request=request)

        cliente = GraphClient(TOKEN, transport=httpx.MockTransport(caida))

        resultado = cliente.account()

        assert isinstance(resultado, GraphError)
        assert TOKEN not in resultado.message
        assert not resultado.token_invalid


class TestRefreshToken:
    def test_renueva_con_ig_refresh_token(self) -> None:
        pedidos: list[httpx.Request] = []
        transporte = _transporte({"/refresh_access_token": (200, RENOVACION)}, pedidos)

        resultado = refresh_token(TOKEN, now=AHORA, transport=transporte)

        assert resultado == RefreshedToken("IGAAtokennuevo", AHORA + timedelta(seconds=5183944))
        (pedido,) = pedidos
        assert pedido.url.params["grant_type"] == "ig_refresh_token"
        assert pedido.url.params["access_token"] == TOKEN

    def test_token_vencido_no_se_puede_renovar(self) -> None:
        pedidos: list[httpx.Request] = []
        transporte = _transporte({"/refresh_access_token": (400, TOKEN_VENCIDO)}, pedidos)

        resultado = refresh_token(TOKEN, now=AHORA, transport=transporte)

        assert isinstance(resultado, GraphError)
        assert resultado.token_invalid

    def test_sin_token_no_llama(self) -> None:
        pedidos: list[httpx.Request] = []

        resultado = refresh_token("", now=AHORA, transport=_transporte({}, pedidos))

        assert isinstance(resultado, GraphError)
        assert pedidos == []
