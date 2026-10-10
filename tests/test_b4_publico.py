"""Slice B4: la capa pública dice de qué está hecho cada n y enlaza a
`/metodologia`. Contrato: `docs/spec-slice-B4.md`.

Valores esperados escritos a mano. Arroz de 1 lb, todo dentro de los últimos
30 días:
- La Habana: 5 filas = 3 `human`, 1 `auto`, 1 `audited`; estados 4 `approved`
  y 1 `anomaly`.
- Santiago de Cuba: 2 filas `human`, `approved`.
Total: 7 anuncios, 5 a mano, 2 aceptados por el sistema (1 ya revisado).
"""

# ruff: noqa: F811 — `client` es la fixture importada de test_public_filter
import html as html_lib
import re
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.models import PriceHistory, Product, Province, SaleFormat
from tests.test_public_filter import _hace, _ingestion, client  # noqa: F401


def _visible(texto: str) -> str:
    sin_estilo = re.sub(r"<(style|script).*?</\1>", " ", texto, flags=re.S)
    plano = re.sub(r"<[^>]+>", " ", sin_estilo)
    return " ".join(html_lib.unescape(plano).split())


def _sembrar(session: Session, *, con_sistema: bool = True) -> None:
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
    habana = Province(code="hab", name="La Habana")
    santiago = Province(code="scu", name="Santiago de Cuba")
    session.add_all([arroz, habana, santiago])
    session.commit()
    fuente = _ingestion(session, hash_="h-b4")
    fuente.sender_hash = "SENDER-HASH-SECRETO"
    habana_filas = [
        ("human", "approved"),
        ("human", "approved"),
        ("human", "approved"),
        ("auto" if con_sistema else "human", "approved"),
        ("audited" if con_sistema else "human", "anomaly"),
    ]
    filas = [(habana, p, s, 9 - i) for i, (p, s) in enumerate(habana_filas)]
    filas += [(santiago, "human", "approved", 8 - i) for i in range(2)]
    for provincia, procedencia, estado, dia in filas:
        session.add(
            PriceHistory(
                provenance=procedencia,
                validation_status=estado,
                product_id=arroz.id,
                province_id=provincia.id,
                price=Decimal("300"),
                currency="CUP",
                source_id=fuente.id,
                unit="lb",
                sale_format=SaleFormat.RETAIL.value,
                quantity=Decimal("1"),
                published_date=_hace(dia),
            )
        )
    session.commit()


VISTAS = ("tablero", "explorar")


def test_el_tablero_dice_de_que_esta_hecho_cada_celda(client: TestClient, session: Session) -> None:
    _sembrar(session)

    texto = _visible(client.get("/precios?vista=tablero").text)

    assert "5 anuncios · 3 a mano · 2 por el sistema" in texto
    assert "2 anuncios · todos a mano" in texto


def test_el_detalle_del_producto_trae_el_desglose(client: TestClient, session: Session) -> None:
    _sembrar(session)

    texto = _visible(client.get("/precios?vista=tablero&productos=arroz").text)

    assert "Por qué confiamos en estas cifras" in texto
    assert "3 revisados a mano" in texto
    assert "1 aceptado por el sistema" in texto
    assert "1 aceptado por el sistema y revisado después por una persona" in texto
    assert "4 pasaron las seis reglas" in texto
    assert "1 con una señal que mirar" in texto


def test_explorar_dice_de_que_esta_hecha_cada_provincia(client: TestClient, session: Session) -> None:
    _sembrar(session)

    texto = _visible(client.get("/precios?vista=explorar&productos=arroz").text)

    assert "5 anuncios · 3 a mano · 2 por el sistema" in texto
    assert "2 anuncios · todos a mano" in texto
    assert "Por qué confiamos en estas cifras" in texto


def test_la_frase_de_procedencia_sale_de_los_conteos(client: TestClient, session: Session) -> None:
    _sembrar(session)

    tablero = _visible(client.get("/precios?vista=tablero").text)
    explorar = _visible(client.get("/precios?vista=explorar&productos=arroz").text)

    frase = (
        "7 anuncios: 5 revisados a mano y 2 aceptados por el sistema "
        "(1 ya revisado después por una persona)"
    )
    assert frase in tablero
    assert frase in explorar
    assert "uno por uno" not in tablero


def test_sin_filas_del_sistema_la_frase_no_nombra_al_sistema(client: TestClient, session: Session) -> None:
    _sembrar(session, con_sistema=False)

    tablero = _visible(client.get("/precios?vista=tablero").text)

    assert "7 anuncios revisados a mano" in tablero
    assert "por el sistema" not in tablero
    assert "aceptados" not in tablero


@pytest.mark.parametrize("vista", VISTAS)
def test_el_enlace_a_metodologia_esta_fuera_del_panel(
    client: TestClient, session: Session, vista: str
) -> None:
    _sembrar(session)

    texto = client.get(f"/precios?vista={vista}&productos=arroz").text
    antes_del_panel = texto.split('aria-labelledby="datos-titulo"')[0]

    assert 'href="/metodologia"' in antes_del_panel


def test_el_panel_sobre_los_datos_tambien_enlaza(client: TestClient, session: Session) -> None:
    _sembrar(session)

    texto = client.get("/precios?vista=tablero").text
    panel = texto.split('aria-labelledby="datos-titulo"')[1]

    assert 'href="/metodologia"' in panel


@pytest.mark.parametrize("vista", VISTAS)
def test_ninguna_salida_expone_emisor_ni_canal(client: TestClient, session: Session, vista: str) -> None:
    _sembrar(session)

    texto = client.get(f"/precios?vista={vista}&productos=arroz").text.lower()

    for prohibido in ("sender-hash-secreto", "sender_hash", "source_reference", "channel_id"):
        assert prohibido not in texto


@pytest.mark.parametrize("vista", VISTAS)
def test_tuteo_y_sin_numeros_compuestos(client: TestClient, session: Session, vista: str) -> None:
    _sembrar(session)

    texto = _visible(client.get(f"/precios?vista={vista}&productos=arroz").text).lower()

    assert "tasa de automatización" not in texto
    assert "confiable" not in texto
    # La capa pública es de tuteo, nunca voseo, en lo que B4 escribe.
    for voseo in ("mirá", "fijate", "tenés", "podés"):
        assert voseo not in texto


def test_una_celda_sin_filas_no_trae_bloque(client: TestClient, session: Session) -> None:
    session.add(Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True))
    session.commit()

    texto = _visible(client.get("/precios?vista=tablero").text)

    assert "Por qué confiamos" not in texto
    assert "por el sistema" not in texto
