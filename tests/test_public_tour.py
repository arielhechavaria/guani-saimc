"""Tour de primera visita del mapa público (`public_home.html`).

pytest no ejecuta JavaScript, así que acá se protege lo que Python sí
controla y el script del pie consume: que el HTML traiga los cuatro pasos
en orden, que cada paso apunte a un elemento que la página de verdad
renderiza (renombrar una clase en la plantilla rompería el tour en silencio:
el paso caería al centro sin error en consola), que el tour viva FUERA de
`#ph-live` (htmx lo reemplaza entero en cada swap), que no cargue nada de
terceros y que la política de privacidad nombre lo que guarda.

Cómo se ve y cómo se comporta (móvil, escritorio, modo oscuro, movimiento
reducido) se verifica en navegador, no acá.
"""

import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.models import (
    ContentType,
    Currency,
    PriceHistory,
    Product,
    Province,
    RawIngestion,
    SaleFormat,
    SourceType,
)
from saimc.models.raw_ingestion import ProcessingStatus
from saimc.web.app import app
from saimc.web.deps import get_db

_TEMPLATES = Path(__file__).resolve().parent.parent / "src" / "saimc" / "web" / "templates"

# Orden y clave de los pasos, definidos por el pedido del slice (qué es Guani,
# cambiar provincia y rango, leer una tarjeta de precio, entrar a un producto)
# antes de escribir la plantilla.
_PASOS = ["que-es", "filtros", "tarjeta", "producto"]


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def datos(session: Session) -> None:
    """Dos productos piloto y dos provincias con dato."""
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
    aceite = Product(name="Aceite", category="grasas", unit="lb", aliases=[], is_pilot=True)
    habana = Province(code="hab", name="La Habana")
    santiago = Province(code="scu", name="Santiago de Cuba")
    session.add_all([arroz, aceite, habana, santiago])
    session.commit()
    fuente = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path="raw/tour.dat",
        content_hash="h-tour",
        content_type=ContentType.TEXT,
        processing_status=ProcessingStatus.CURATED,
        captured_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    session.add(fuente)
    session.flush()
    for producto, provincia, precio, atras in (
        (arroz, habana, "300", 3),
        (arroz, santiago, "280", 4),
        (aceite, habana, "900", 2),
    ):
        session.add(
            PriceHistory(
                provenance="human",
                product_id=producto.id,
                province_id=provincia.id,
                price=Decimal(precio),
                currency=Currency.CUP.value,
                source_id=fuente.id,
                unit="lb",
                sale_format=SaleFormat.RETAIL.value,
                quantity=Decimal("1"),
                published_date=date.today() - timedelta(days=atras),
            )
        )
    session.commit()


@pytest.fixture
def mapa(client: TestClient, datos: None) -> str:
    """El mapa tal como lo ve quien llega por primera vez: sin `productos`, así
    que no hay panel de precios (`product_grids` sale de los productos activos)."""
    respuesta = client.get("/precios", params={"vista": "mapa"})
    assert respuesta.status_code == 200
    return respuesta.text


@pytest.fixture
def mapa_con_producto(client: TestClient, datos: None) -> str:
    """El mismo mapa con un producto elegido: el panel lateral existe y los
    pasos 3 y 4 tienen a qué apuntar. Es lo que el tour deja en pantalla al
    abrir el primer producto."""
    respuesta = client.get("/precios", params={"vista": "mapa", "productos": "arroz"})
    assert respuesta.status_code == 200
    return respuesta.text


class _Ancestros(HTMLParser):
    """Para un id dado: los ids de todos sus ancestros en el HTML."""

    _VACIOS = frozenset({"meta", "link", "input", "br", "img", "hr", "source", "wbr", "area", "col"})

    def __init__(self, buscado: str) -> None:
        super().__init__()
        self.buscado = buscado
        self.pila: list[tuple[str, str]] = []
        self.ancestros: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._VACIOS:
            return
        ident = dict(attrs).get("id") or ""
        if ident == self.buscado and self.ancestros is None:
            self.ancestros = [i for _t, i in self.pila if i]
        self.pila.append((tag, ident))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.pila) - 1, -1, -1):
            if self.pila[i][0] == tag:
                del self.pila[i:]
                return


def _ancestros_de(html: str, ident: str) -> list[str]:
    parser = _Ancestros(ident)
    parser.feed(html)
    assert parser.ancestros is not None, f"no hay #{ident} en la página"
    return parser.ancestros


def _pasos(html: str) -> list[dict[str, str]]:
    plantilla = re.search(r'<template id="ph-tour-steps">(.*?)</template>', html, re.S)
    assert plantilla is not None, "falta <template id=\"ph-tour-steps\">"
    pasos = []
    for apertura in re.finditer(r"<li\b([^>]*\bdata-tour-step=[^>]*)>", plantilla.group(1)):
        pasos.append(dict(re.findall(r'data-([\w-]+)="([^"]*)"', apertura.group(1))))
    return pasos


def _bloque_css_del_tour() -> str:
    fuente = (_TEMPLATES / "public_home.html").read_text(encoding="utf-8")
    inicio = fuente.index("/* ── Tour")
    fin = fuente.index("/* ── /Tour */")
    return fuente[inicio:fin]


class TestMarcadoDelTour:
    def test_trae_los_cuatro_pasos_en_orden(self, mapa: str) -> None:
        assert [p["tour-step"] for p in _pasos(mapa)] == _PASOS

    def test_cada_paso_tiene_titulo_y_texto(self, mapa: str) -> None:
        plantilla = re.search(r'<template id="ph-tour-steps">(.*?)</template>', mapa, re.S)
        assert plantilla is not None
        items = re.findall(r"<li\b[^>]*data-tour-step=.*?</li>", plantilla.group(1), re.S)
        assert len(items) == len(_PASOS)
        for item in items:
            assert re.search(r"<h3>\s*\S", item), item
            assert re.search(r"<p>\s*\S", item), item

    def test_el_texto_tutea_y_no_promete_revision_uno_por_uno(self, mapa: str) -> None:
        """Mismo criterio que la landing (`test_landing.py`, narrativa): tuteo,
        y nada que diga que una persona revisa cada precio, que ya no es cierto."""
        plantilla = re.search(r'<template id="ph-tour-steps">(.*?)</template>', mapa, re.S)
        assert plantilla is not None
        texto = re.sub(r"<[^>]+>", " ", plantilla.group(1)).lower()

        for voseo in ("mirá", "elegí", "tocá", "entrá", "podés", "tenés"):
            assert voseo not in texto
        assert "uno por uno" not in texto
        assert "revisa cada" not in texto

    def test_el_primer_paso_es_un_mensaje_sin_elemento_apuntado(self, mapa: str) -> None:
        primero = _pasos(mapa)[0]
        assert primero.get("target", "") == ""

    def test_los_otros_pasos_apuntan_a_algo_que_la_pagina_renderiza(
        self, mapa: str, mapa_con_producto: str
    ) -> None:
        """Las clases de cada selector (objetivo y respaldo) existen en el
        marcado real de la primera visita o del mapa con un producto abierto.
        Si alguien renombra `.ph-pane-head`, el paso 3 deja de encontrar su
        elemento y cae al centro sin avisar: esto lo hace ruido."""
        clases_en_la_pagina = {
            clase
            for html in (mapa, mapa_con_producto)
            for atributo in re.findall(r'<[^>]*\bclass="([^"]*)"', html)
            for clase in atributo.split()
        }
        for paso in _pasos(mapa)[1:]:
            assert paso["target"], f"el paso {paso['tour-step']} no apunta a nada"
            for selector in (paso["target"], paso.get("fallback", "")):
                for clase in re.findall(r"\.([\w-]+)", selector):
                    assert clase in clases_en_la_pagina, (paso["tour-step"], selector, clase)

    def test_el_paso_de_producto_apunta_al_enlace_a_la_pagina_del_producto(
        self, mapa_con_producto: str
    ) -> None:
        paso = next(p for p in _pasos(mapa_con_producto) if p["tour-step"] == "producto")
        assert "/producto/" in paso["target"]
        assert re.search(r'<a class="ph-fpanel-detail" href="/producto/[\w-]+', mapa_con_producto)

    def test_el_paso_de_la_tarjeta_abre_un_producto_si_la_primera_visita_no_trae_panel(
        self, mapa: str
    ) -> None:
        """En la primera visita no hay panel de precios (ver `mapa`): el paso 3
        lo abre tocando el primer producto, y eso necesita un chip con `hx-get`."""
        assert 'class="ph-pane-head"' not in mapa
        paso = next(p for p in _pasos(mapa) if p["tour-step"] == "tarjeta")
        assert paso.get("prepare") == "open-first-product"
        assert re.search(r'<a class="ph-chip-toggle[^"]*"[^>]*\bhx-get=', mapa)

    def test_el_tour_vive_fuera_de_ph_live(self, mapa: str) -> None:
        """htmx reemplaza `#ph-live` entero en cada swap: un tour adentro se
        reiniciaría con cada cambio de provincia o de rango."""
        assert "ph-live" not in _ancestros_de(mapa, "ph-tour")

    def test_arranca_oculto(self, mapa: str) -> None:
        apertura = re.search(r'<div\b[^>]*\bid="ph-tour"[^>]*>', mapa)
        assert apertura is not None
        assert re.search(r"\bhidden\b", apertura.group(0))

    def test_es_un_dialogo_modal_con_nombre_accesible(self, mapa: str) -> None:
        apertura = re.search(r'<div\b[^>]*\bid="ph-tour"[^>]*>', mapa)
        assert apertura is not None
        assert 'role="dialog"' in apertura.group(0)
        assert 'aria-modal="true"' in apertura.group(0)
        assert "aria-labelledby=" in apertura.group(0)

    def test_hay_boton_para_repetirlo_dentro_del_menu_del_compas(self, mapa: str) -> None:
        boton = re.search(r'<button\b[^>]*\bdata-tour-open\b[^>]*>', mapa)
        assert boton is not None, "falta el botón para repetir el tour"
        assert 'type="button"' in boton.group(0)
        assert "aria-label=" in boton.group(0)
        assert "ph-live" in _ancestros_de(mapa, "ph-tour-reopen")

    def test_los_controles_del_tour_estan_todos(self, mapa: str) -> None:
        for atributo in ("data-tour-next", "data-tour-prev", "data-tour-skip"):
            assert re.search(rf"<button\b[^>]*\b{atributo}\b", mapa), atributo


class TestSoloEnElMapa:
    def test_el_tablero_no_lo_trae(self, client: TestClient, mapa: str) -> None:
        tablero = client.get("/precios", params={"vista": "tablero"}).text
        assert 'id="ph-tour"' not in tablero
        assert "data-tour-open" not in tablero

    def test_explorar_no_lo_trae(self, client: TestClient, mapa: str) -> None:
        explorar = client.get("/precios", params={"vista": "explorar"}).text
        assert 'id="ph-tour"' not in explorar

    def test_la_landing_no_lo_trae(self, client: TestClient, mapa: str) -> None:
        assert 'id="ph-tour"' not in client.get("/").text


class TestSinTerceros:
    def test_el_tour_no_carga_scripts_ni_estilos_externos(self, mapa: str) -> None:
        """Sin librerías ni CDN (decisión cerrada 1 y DESIGN.md regla 6)."""
        cargas = re.findall(r'<script\b[^>]*\bsrc="([^"]+)"', mapa)
        cargas += re.findall(r'<link\b[^>]*\brel="stylesheet"[^>]*\bhref="([^"]+)"', mapa)
        assert cargas  # htmx, Leaflet y la hoja pública
        for url in cargas:
            assert url.startswith("/static/"), url

    def test_el_script_del_tour_guarda_solo_la_clave_pedida(self) -> None:
        fuente = (_TEMPLATES / "public_home.html").read_text(encoding="utf-8")
        assert '"guani_tour"' in fuente


class TestEstiloDelTour:
    def test_solo_usa_tokens_nunca_colores_sueltos(self) -> None:
        """DESIGN.md: nunca un color con valor suelto en una plantilla."""
        css = _bloque_css_del_tour()
        assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", css)
        assert not re.findall(r"\brgba?\(", css)
        assert "var(--ph-" in css

    def test_respeta_prefers_reduced_motion(self) -> None:
        css = _bloque_css_del_tour()
        reducido = re.search(r"@media \(prefers-reduced-motion: reduce\)\s*{(.*)", css, re.S)
        assert reducido is not None, "el tour anima y no declara prefers-reduced-motion"
        assert ".ph-tour" in reducido.group(1)

    def test_los_botones_miden_44px_de_alto(self) -> None:
        assert "min-height: 44px" in _bloque_css_del_tour()


class TestPoliticaDePrivacidad:
    def test_nombra_la_clave_que_guarda_el_tour(self, client: TestClient) -> None:
        texto = client.get("/privacidad").text
        assert "<code>guani_tour</code>" in texto

    def test_dice_para_que_sirve_y_que_se_puede_repetir(self, client: TestClient) -> None:
        texto = re.sub(r"<[^>]+>", " ", client.get("/privacidad").text).lower()
        assert "recorrido" in texto  # el tour, en las palabras del visitante
        assert "repet" in texto
