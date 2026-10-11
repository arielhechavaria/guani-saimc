"""Landing pública en `/` y mudanza del tablero a `/precios` (2026-10-05).

Lo que estos casos protegen:

- `/` presenta a Guani y lleva a `/precios`; no sirve ni una cifra inventada ni
  un marcador del prototipo («000», «[PROVINCIA]», «Cifras de ejemplo»).
- `/precios` es la home de siempre, con los mismos parámetros.
- Un enlace ya compartido (`/?vista=…`, `/?producto=…`) sigue llegando a donde
  llegaba: redirige a `/precios` con el query string intacto.
- Las burbujas del héroe salen de anuncios reales: dicen el rango, la provincia y
  cuántos anuncios hay detrás.
- Los enlaces de redes y contacto solo se dibujan si están configurados.

Los valores esperados de `landing_view` están escritos a mano a partir de las
observaciones de cada caso, antes de que existiera el módulo. Las fechas van
relativas a hoy, como en `test_public_precio.py`.
"""

import re
import shutil
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.cells import RetailObservation
from saimc.config import Settings
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
from saimc.web import landing_view
from saimc.web.app import app
from saimc.web.deps import get_db
from saimc.web.landing_view import (
    build_footer_links,
    build_landing_coverage,
    build_landing_entries,
    build_landing_flow,
    build_landing_price,
)
from saimc.web.routers import public

#: Lo que el prototipo traía de relleno y no puede salir en producción.
MARCADORES = ("000", "[TU", "[PROVINCIA]", "[AÑO]", "Cifras de ejemplo")


def _visible(html: str) -> str:
    """El texto que lee el visitante: sin estilos, sin scripts y sin etiquetas.
    Los marcadores se buscan acá y no en el HTML crudo porque el `?v=` de la
    hoja de estilos es una marca de tiempo y puede traer tres ceros seguidos."""
    sin_bloques = re.sub(r"<(style|script)\b.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", sin_bloques)


def _globos(html: str) -> str:
    """El texto de los globos de la portada."""
    return _visible(" ".join(re.findall(r'<div class="lp-globe[^"]*">.*?</div>', html, flags=re.S)))


def _settings(**valores: str) -> Settings:
    """Configuración sin leer ningún `.env`: el del desarrollador no puede
    decidir si un test de enlaces pasa."""
    return Settings(_env_file=None, **valores)  # type: ignore[arg-type, call-arg]


@pytest.fixture
def client(session: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    monkeypatch.setattr(public, "get_settings", lambda: _settings())
    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def datos(session: Session) -> None:
    """Arroz en La Habana (280, 300, 320) y en Santiago (400); Pollo en La
    Habana (700, 720). Arroz es el de más anuncios: 4 contra 2."""
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
    pollo = Product(name="Pollo", category="carnes", unit="lb", aliases=[], is_pilot=True)
    habana = Province(code="hab", name="La Habana")
    santiago = Province(code="scu", name="Santiago de Cuba")
    session.add_all([arroz, pollo, habana, santiago])
    session.commit()
    origen = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path="raw/l.dat",
        content_hash="hash-landing",
        content_type=ContentType.TEXT,
        processing_status=ProcessingStatus.CURATED,
        captured_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    session.add(origen)
    session.flush()
    hoy = date.today()

    def precio(producto: Product, provincia: Province, hace: int, valor: str) -> None:
        session.add(
            PriceHistory(
                provenance="human",
                product_id=producto.id,
                province_id=provincia.id,
                price=Decimal(valor),
                currency=Currency.CUP.value,
                source_id=origen.id,
                unit="lb",
                sale_format=SaleFormat.RETAIL.value,
                quantity=Decimal("1"),
                published_date=hoy - timedelta(days=hace),
            )
        )

    precio(arroz, habana, 12, "280")
    precio(arroz, habana, 8, "300")
    precio(arroz, habana, 5, "320")
    precio(arroz, santiago, 3, "400")
    precio(pollo, habana, 4, "700")
    precio(pollo, habana, 2, "720")
    session.commit()


def _obs(producto: str, provincia: str | None, valor: str, *, moneda: str = "CUP") -> RetailObservation:
    return RetailObservation(
        product_name=producto,
        province_name=provincia,
        currency=moneda,
        unit="lb",
        unconverted=False,
        day=date(2026, 10, 1),
        unit_price=Decimal(valor),
        evidence_id=uuid.uuid4(),
        evidence_is_public=True,
    )


class TestLaTarjetaDePrecio:
    def test_elige_el_producto_con_mas_anuncios_y_su_provincia_con_mas_anuncios(self) -> None:
        obs = [
            _obs("Arroz", "La Habana", "280"),
            _obs("Arroz", "La Habana", "300"),
            _obs("Arroz", "La Habana", "320"),
            _obs("Arroz", "Santiago de Cuba", "400"),
            _obs("Pollo", "La Habana", "700"),
            _obs("Pollo", "La Habana", "720"),
        ]

        precio = build_landing_price(obs)

        assert precio is not None
        assert (precio.product_name, precio.province_name) == ("Arroz", "La Habana")
        assert (precio.minimum_text, precio.maximum_text) == ("280", "320")
        assert precio.median_text == "300"
        assert precio.count == 3
        assert (precio.currency, precio.unit) == ("CUP", "lb")

    def test_lleva_el_rotulo_de_muestra_en_formacion(self) -> None:
        precio = build_landing_price([_obs("Arroz", "La Habana", v) for v in ("280", "320")])

        assert precio is not None
        assert precio.state == "muestra en formación"

    def test_sin_observaciones_no_hay_tarjeta(self) -> None:
        assert build_landing_price([]) is None

    def test_un_solo_anuncio_no_es_un_rango(self) -> None:
        """Con una observación el mínimo y el máximo son el mismo número:
        presentarlo como rango inventa una variación que nadie observó."""
        assert build_landing_price([_obs("Arroz", "La Habana", "300")]) is None

    def test_varios_anuncios_al_mismo_precio_tampoco_son_un_rango(self) -> None:
        obs = [_obs("Arroz", "La Habana", "300"), _obs("Arroz", "La Habana", "300")]

        assert build_landing_price(obs) is None

    def test_una_fila_sin_provincia_no_sostiene_la_tarjeta(self) -> None:
        obs = [_obs("Arroz", None, "280"), _obs("Arroz", None, "320")]

        assert build_landing_price(obs) is None

    def test_no_mezcla_monedas_en_un_mismo_rango(self) -> None:
        obs = [
            _obs("Arroz", "La Habana", "280"),
            _obs("Arroz", "La Habana", "320"),
            _obs("Arroz", "La Habana", "1", moneda="USD"),
        ]

        precio = build_landing_price(obs)

        assert precio is not None
        assert (precio.minimum_text, precio.maximum_text, precio.count) == ("280", "320", 2)

    def test_si_el_producto_mas_anunciado_no_tiene_rango_cae_al_siguiente(self) -> None:
        obs = [
            _obs("Arroz", "La Habana", "300"),
            _obs("Arroz", "Santiago de Cuba", "300"),
            _obs("Arroz", "Holguín", "300"),
            _obs("Pollo", "La Habana", "700"),
            _obs("Pollo", "La Habana", "720"),
        ]

        precio = build_landing_price(obs)

        assert precio is not None
        assert (precio.product_name, precio.minimum_text, precio.maximum_text) == ("Pollo", "700", "720")


class TestElPrecioDisparatadoNoMueveElRango:
    """El caso real del 2026-10-05: una sola fila absurda dejaba la portada en
    «Arroz · La Habana 210 – 178.880 CUP/lb». El resto de la capa pública
    (tablero, explorar, `/producto`) ya saca la mediana y el rango de lo que
    cae dentro del cerco de `/metodologia`; la portada tiene que hacer lo mismo.

    Los valores esperados salen de la regla publicada, a mano: con seis
    precios entre 280 y 320 la mitad central es angosta, el margen es el
    mínimo (×2) y el cerco llega a unos 640; 178.880 queda fuera por mucho.
    """

    NORMALES = ("280", "300", "300", "310", "320")

    def test_la_fila_absurda_no_entra_en_el_rango_ni_en_la_mediana(self) -> None:
        obs = [_obs("Arroz", "La Habana", v) for v in (*self.NORMALES, "178880")]

        precio = build_landing_price(obs)

        assert precio is not None
        assert (precio.minimum_text, precio.maximum_text) == ("280", "320")
        assert precio.median_text == "300"

    def test_la_muestra_cuenta_todo_y_dice_cuantos_quedaron_fuera(self) -> None:
        """Igual que la cifra del tablero: el anuncio no se esconde, se cuenta
        aparte."""
        obs = [_obs("Arroz", "La Habana", v) for v in (*self.NORMALES, "178880")]

        precio = build_landing_price(obs)

        assert precio is not None
        assert (precio.count, precio.outliers) == (6, 1)

    def test_el_precio_ya_no_lleva_datos_solo_de_dibujo(self) -> None:
        """La sección «Así se lee un precio» se quitó: nadie dibuja ya los
        puntos, la barra ni la mediana sobre ella."""
        precio = build_landing_price([_obs("Arroz", "La Habana", v) for v in ("280", "300", "320")])

        assert precio is not None
        for campo in ("dots", "median_left", "bar_left", "bar_width"):
            assert not hasattr(precio, campo), campo

    def test_sin_precios_disparatados_no_hay_ninguno_fuera(self) -> None:
        precio = build_landing_price([_obs("Arroz", "La Habana", v) for v in self.NORMALES])

        assert precio is not None
        assert (precio.count, precio.outliers) == (5, 0)

    def test_un_rango_que_solo_existe_por_la_fila_absurda_no_es_un_rango(self) -> None:
        """Cinco anuncios a 300 y uno a 178.880: dentro del cerco todos valen
        lo mismo, así que esa celda no sirve de ejemplo y se pasa a la otra."""
        obs = [
            *[_obs("Arroz", "La Habana", v) for v in ("300", "300", "300", "300", "300", "178880")],
            _obs("Pollo", "La Habana", "700"),
            _obs("Pollo", "La Habana", "720"),
        ]

        precio = build_landing_price(obs)

        assert precio is not None
        assert (precio.product_name, precio.minimum_text, precio.maximum_text) == ("Pollo", "700", "720")


class TestLaCobertura:
    """Cuántos productos y provincias tienen dato: sale de las observaciones,
    nunca del texto."""

    def test_cuenta_productos_y_provincias_distintos(self) -> None:
        obs = [
            _obs("Arroz", "La Habana", "280"),
            _obs("Arroz", "La Habana", "320"),
            _obs("Arroz", "Santiago de Cuba", "400"),
            _obs("Pollo", "La Habana", "700"),
        ]

        cobertura = build_landing_coverage(obs)

        assert cobertura is not None
        assert (cobertura.products, cobertura.provinces) == (2, 2)

    def test_una_fila_sin_provincia_no_suma(self) -> None:
        cobertura = build_landing_coverage([_obs("Arroz", None, "280"), _obs("Pollo", "La Habana", "700")])

        assert cobertura is not None
        assert (cobertura.products, cobertura.provinces) == (1, 1)

    def test_sin_observaciones_no_hay_cobertura(self) -> None:
        assert build_landing_coverage([]) is None


class TestLosEnlacesDelPie:
    def test_sin_nada_configurado_no_hay_enlaces(self) -> None:
        enlaces = build_footer_links(_settings())

        assert enlaces.email is None
        assert enlaces.social == ()
        assert not enlaces.any

    def test_solo_aparece_la_red_configurada(self) -> None:
        enlaces = build_footer_links(_settings(link_telegram="https://t.me/guani"))

        assert [(e.label, e.href) for e in enlaces.social] == [("Telegram", "https://t.me/guani")]
        assert enlaces.email is None

    def test_el_orden_es_facebook_whatsapp_telegram(self) -> None:
        enlaces = build_footer_links(
            _settings(
                link_telegram="https://t.me/guani",
                link_facebook="https://facebook.com/guani",
                link_whatsapp="https://whatsapp.com/channel/abc",
            )
        )

        assert [e.label for e in enlaces.social] == ["Facebook", "WhatsApp", "Telegram"]

    def test_el_correo_se_conserva_recortado(self) -> None:
        enlaces = build_footer_links(_settings(contact_email="  hola@guani.example  "))

        assert enlaces.email == "hola@guani.example"
        assert enlaces.any

    @pytest.mark.parametrize("valor", ["", "   ", "javascript:alert(1)", "facebook.com/guani", "//x.example"])
    def test_un_enlace_que_no_es_http_no_se_dibuja(self, valor: str) -> None:
        assert build_footer_links(_settings(link_facebook=valor)).social == ()

    @pytest.mark.parametrize("valor", ["", "sin-arroba", "a b@guani.example", "x@y.example?subject=<b>"])
    def test_un_correo_mal_formado_no_se_dibuja(self, valor: str) -> None:
        assert build_footer_links(_settings(contact_email=valor)).email is None


class TestLaLandingEnLaRaiz:
    def test_presenta_a_guani_y_lleva_a_los_precios(self, client: TestClient, datos: None) -> None:
        respuesta = client.get("/")

        assert respuesta.status_code == 200
        assert "Precios reales del mercado informal cubano." in respuesta.text
        assert 'href="/precios"' in respuesta.text

    def test_no_es_el_tablero(self, client: TestClient, datos: None) -> None:
        assert "Qué cuesta hoy" not in client.get("/").text

    def test_no_sirve_marcadores_del_prototipo(self, client: TestClient, datos: None) -> None:
        texto = _visible(client.get("/").text)

        for marcador in MARCADORES:
            assert marcador not in texto

    def test_no_pide_nada_a_una_red_ajena(self, client: TestClient, datos: None) -> None:
        """Ni fuentes ni iconos por CDN: lo único externo son enlaces `<a>`."""
        texto = client.get("/").text

        assert "fonts.googleapis.com" not in texto
        assert "fonts.gstatic.com" not in texto
        assert not re.search(r'<(link|script|img)[^>]+(href|src)="https?://', texto)

    def test_la_cookie_de_vista_no_saca_de_la_landing(self, client: TestClient, datos: None) -> None:
        client.cookies.set("vista", "mapa")
        client.cookies.set("provincia", "la-habana")

        respuesta = client.get("/", follow_redirects=False)

        assert respuesta.status_code == 200
        assert "Precios reales del mercado informal cubano." in respuesta.text

    def test_las_burbujas_muestran_el_rango_real_con_su_muestra(
        self, client: TestClient, datos: None
    ) -> None:
        globos = _globos(client.get("/").text)

        assert re.search(r"Arroz · 280–320 CUP/lb", globos)
        assert "La Habana" in globos
        assert re.search(r"\b3\s+anuncios", globos)

    def test_no_juzga_el_precio(self, client: TestClient, datos: None) -> None:
        globos = _globos(client.get("/").text)

        for palabra in ("barato", "caro", "conviene", "oferta"):
            assert palabra not in globos.lower()

    def test_los_productos_piloto_enlazan_al_tablero(self, client: TestClient, datos: None) -> None:
        texto = client.get("/").text
        chips = texto.split('class="lp-chips"')[1].split("</div>")[0]

        assert re.search(r'href="/precios\?productos=arroz&amp;[^"]*vista=tablero"[^>]*>Arroz<', chips)
        assert re.search(r'href="/precios\?productos=pollo&amp;[^"]*vista=tablero"[^>]*>Pollo<', chips)

    def test_las_baldosas_llevan_a_cada_vista_y_a_la_descarga(self, client: TestClient, datos: None) -> None:
        texto = client.get("/").text

        destinos = ("/precios?vista=tablero", "/precios?vista=mapa", "/precios?vista=explorar", "/datos.csv")
        for destino in destinos:
            assert f'href="{destino}"' in texto

    def test_el_pie_lleva_el_anio_y_el_credito_a_eltoque(self, client: TestClient, datos: None) -> None:
        texto = client.get("/").text

        assert f"© {date.today().year} Guani" in texto
        assert 'href="https://eltoque.com/"' in texto

    def test_lleva_los_metadatos_para_compartir(self, client: TestClient, datos: None) -> None:
        texto = client.get("/").text

        assert re.search(r"<title>Guani — [^<]+</title>", texto)
        assert re.search(r'<meta name="description" content="[^"]{40,}"', texto)
        assert re.search(r'<meta property="og:title" content="Guani — [^"]+"', texto)
        assert re.search(r'<meta property="og:description" content="[^"]{40,}"', texto)
        assert '<meta property="og:type" content="website"' in texto
        assert '<meta property="og:url" content="http://testserver/"' in texto

    def test_no_expone_la_herramienta_del_operador(self, client: TestClient, datos: None) -> None:
        texto = client.get("/").text

        for ruta in ("/curation", "/control", "/catalog", "/docs", "/marketing"):
            assert f'href="{ruta}' not in texto

    def test_una_fila_disparatada_no_mueve_el_rango_de_la_portada(
        self, client: TestClient, datos: None, session: Session
    ) -> None:
        """Arroz en La Habana con 280, 290, 300, 305, 310, 320 y una fila a
        178.880: el globo sigue en 280–320 y cuenta siete anuncios; la fila
        de fuera no entra al rango."""
        arroz = session.query(Product).filter_by(name="Arroz").one()
        habana = session.query(Province).filter_by(name="La Habana").one()
        origen = session.query(RawIngestion).one()
        for valor in ("290", "305", "310", "178880"):
            session.add(
                PriceHistory(
                    provenance="human",
                    product_id=arroz.id,
                    province_id=habana.id,
                    price=Decimal(valor),
                    currency=Currency.CUP.value,
                    source_id=origen.id,
                    unit="lb",
                    sale_format=SaleFormat.RETAIL.value,
                    quantity=Decimal("1"),
                    published_date=date.today() - timedelta(days=1),
                )
            )
        session.commit()

        globos = _globos(client.get("/").text)

        assert re.search(r"Arroz · 280–320 CUP/lb", globos)
        assert "178" not in globos
        assert re.search(r"\b7\s+anuncios", globos)


class TestPorQueFiarte:
    """La sección de fortalezas (2026-10-05, `docs/decisions.md`). Cada tarjeta
    dice algo que el código hace hoy, con su alcance real."""

    TITULOS = (
        "Cada cifra enseña sus anuncios",
        "Provincia por provincia",
        "Compara lo comparable",
        "Sabes quién la revisó",
        "Un precio disparatado no manda",
        "Todo en pesos cubanos",
    )

    def _seccion(self, client: TestClient) -> str:
        return client.get("/").text.split('id="lp-why"')[1].split("</section>")[0]

    def test_trae_el_titulo_y_las_seis_tarjetas(self, client: TestClient, datos: None) -> None:
        seccion = self._seccion(client)

        assert "Por qué puedes fiarte de estas cifras" in _visible(seccion)
        assert re.findall(r'<h3 class="lp-why-title">([^<]+)</h3>', seccion) == list(self.TITULOS)
        assert seccion.count('class="lp-why-card') == 6
        # Un icono y una oración por tarjeta.
        assert seccion.count("<svg") >= 6
        assert seccion.count('<p class="lp-why-text">') == 6

    def test_va_despues_del_recorrido_y_antes_de_las_preguntas(self, client: TestClient, datos: None) -> None:
        texto = client.get("/").text

        assert texto.index('id="lp-work"') < texto.index('id="lp-why"') < texto.index("¿Qué es Guani?")

    def test_no_promete_abrir_los_anuncios_que_son_imagen(self, client: TestClient, datos: None) -> None:
        """`/anuncio/{id}` solo publica anuncios de texto: la tarjeta no puede
        decir que todo precio enlaza a su anuncio."""
        seccion = _visible(self._seccion(client))

        assert "de texto" in seccion
        assert "cada precio enlaza" not in seccion.lower()

    def test_las_monedas_son_las_que_se_convierten(self, client: TestClient, datos: None) -> None:
        seccion = _visible(self._seccion(client))

        for moneda in ("USD", "EUR", "MLC", "CUP", "elTOQUE"):
            assert moneda in seccion, moneda

    def test_la_cobertura_sale_de_la_base(self, client: TestClient, datos: None) -> None:
        """La fixture tiene 2 productos y 2 provincias con anuncios."""
        seccion = re.sub(r"\s+", " ", _visible(self._seccion(client)))

        assert "2 productos en 2 provincias" in seccion

    def test_sin_datos_no_afirma_ninguna_cifra(self, client: TestClient) -> None:
        seccion = _visible(self._seccion(client))

        assert "productos en" not in seccion
        assert not re.search(r"\d", seccion.replace("900 ml", ""))

    def test_enlaza_a_la_metodologia_y_no_carga_scripts(self, client: TestClient, datos: None) -> None:
        seccion = self._seccion(client)

        assert 'href="/metodologia"' in seccion
        assert "<script" not in seccion

    def test_no_usa_el_argumento_para_instituciones(self, client: TestClient, datos: None) -> None:
        seccion = _visible(self._seccion(client)).lower()

        assert "mvp" not in seccion
        assert "producción" not in seccion


def _prohibidas_por_nombre() -> tuple[str, ...]:
    """Fragmentos del nombre personal del operador y de su usuario de GitHub,
    armados por partes para que no queden escritos en el repositorio. Se
    comparan en minúsculas."""
    apellido = "".join(("hec", "hav", "ar"))
    nombre = "".join(("ar", "iel"))
    return (apellido, rf"\b{nombre}\b", "".join(("git", "hub")), "@" + "".join(("gm", "ail")))


#: Lo que la capa pública no promete (`docs/decisions.md`, 2026-10-05): el
#: código no está publicado, y ni la predicción ni la oferta para empresas
#: existen. Se busca en el texto visible, en minúsculas.
NO_SE_AFIRMA = (
    "código abierto",
    "codigo abierto",
    "open source",
    "repositorio",
    "predicci",
    "predecir",
    "pronóstic",
    "pronostic",
    "proyecto abierto",
    "sitio abierto",
    "todo el país",
    "toda cuba",
    "cobertura nacional",
)


class TestLoQueLaCapaPublicaNoDice:
    @pytest.fixture
    def rutas(self, session: Session, datos: None, monkeypatch: pytest.MonkeyPatch) -> tuple[str, ...]:
        monkeypatch.setattr(
            public,
            "get_settings",
            lambda: _settings(contact_email="hola@guani.example", link_telegram="https://t.me/guani"),
        )
        anuncio = session.query(RawIngestion).one()
        return (
            "/",
            "/precios?vista=tablero",
            "/precios?vista=mapa",
            "/precios?vista=explorar",
            "/producto/arroz",
            f"/anuncio/{anuncio.id}",
            "/quienes-somos",
            "/metodologia",
            "/preguntas",
            "/terminos",
            "/privacidad",
            "/uso-de-datos",
            "/contacto",
            "/datos.csv",
        )

    def test_ninguna_respuesta_trae_el_nombre_personal(
        self, client: TestClient, rutas: tuple[str, ...]
    ) -> None:
        for ruta in rutas:
            respuesta = client.get(ruta)
            assert respuesta.status_code == 200, ruta
            cabeceras = " ".join(f"{k}: {v}" for k, v in respuesta.headers.items())
            todo = (respuesta.text + " " + cabeceras).lower()
            for patron in _prohibidas_por_nombre():
                assert not re.search(patron, todo), f"{ruta}: aparece un dato personal"

    def test_ninguna_pagina_promete_lo_que_no_existe(
        self, client: TestClient, rutas: tuple[str, ...]
    ) -> None:
        for ruta in rutas:
            texto = re.sub(r"\s+", " ", _visible(client.get(ruta).text))
            for frase in NO_SE_AFIRMA:
                assert frase not in texto.lower(), f"{ruta}: {frase}"
            # «API» como oferta: la sigla suelta, en mayúsculas.
            assert not re.search(r"\bAPI\b", texto), ruta

    def test_los_metadatos_tampoco(self, client: TestClient, rutas: tuple[str, ...]) -> None:
        for ruta in rutas:
            metas = " ".join(re.findall(r"<meta[^>]+>", client.get(ruta).text)).lower()
            for frase in NO_SE_AFIRMA:
                assert frase not in metas, f"{ruta}: {frase}"


class TestLaLandingSinDatos:
    def test_con_la_base_vacia_responde_sin_inventar_cifras(self, client: TestClient) -> None:
        respuesta = client.get("/")

        assert respuesta.status_code == 200
        assert "Precios reales del mercado informal cubano." in respuesta.text
        for marcador in MARCADORES:
            assert marcador not in _visible(respuesta.text)

    def test_con_la_base_vacia_las_burbujas_no_traen_numeros(self, client: TestClient) -> None:
        assert not re.search(r"\d", _globos(client.get("/").text))

    def test_con_la_base_vacia_no_hay_chips_de_producto(self, client: TestClient) -> None:
        assert 'class="lp-chips"' not in client.get("/").text

    def test_productos_sin_anuncios_dan_chips_pero_no_cifras(
        self, client: TestClient, session: Session
    ) -> None:
        session.add(Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True))
        session.commit()

        texto = client.get("/").text
        globos = _globos(texto)

        assert ">Arroz<" in texto.split('class="lp-chips"')[1].split("</div>")[0]
        assert "Arroz" in globos
        assert "CUP" not in globos
        assert not re.search(r"\d", globos)

    def test_los_globos_de_la_portada_dicen_el_rango_la_provincia_y_la_muestra(
        self, client: TestClient, datos: None
    ) -> None:
        globos = _globos(client.get("/").text)

        assert re.search(r"Arroz · 280–320 CUP/lb", globos)
        assert "La Habana" in globos
        assert "3 anuncios" in globos


class TestLosEnlacesDeRedesEnLaPagina:
    def test_sin_variables_no_aparece_la_columna(self, client: TestClient) -> None:
        texto = client.get("/").text

        assert 'aria-label="Redes"' not in texto
        for red in ("Facebook", "WhatsApp", "Telegram"):
            assert f">{red}<" not in texto

    def test_con_una_configurada_aparece_solo_esa(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            public, "get_settings", lambda: _settings(link_facebook="https://facebook.com/guani")
        )

        texto = client.get("/").text

        assert 'href="https://facebook.com/guani"' in texto
        assert ">Facebook<" in texto
        assert ">WhatsApp<" not in texto
        assert ">Telegram<" not in texto
        assert "mailto:" not in texto

    def test_el_correo_configurado_sale_como_mailto(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(public, "get_settings", lambda: _settings(contact_email="hola@guani.example"))

        assert 'href="mailto:hola@guani.example"' in client.get("/").text


class TestElTableroEnPrecios:
    def test_precios_es_la_home_de_siempre(self, client: TestClient, datos: None) -> None:
        respuesta = client.get("/precios")

        assert respuesta.status_code == 200
        assert "Qué cuesta hoy" in respuesta.text
        assert "Precios reales del mercado informal cubano." not in respuesta.text

    def test_los_parametros_de_siempre_siguen_andando(self, client: TestClient, datos: None) -> None:
        mapa = client.get("/precios?vista=mapa")
        explorar = client.get("/precios?vista=explorar&productos=arroz")
        detalle = client.get("/precios?vista=tablero&productos=arroz")

        assert mapa.status_code == explorar.status_code == detalle.status_code == 200
        assert 'id="ph-map-data"' in mapa.text
        assert "Explorar datos" in explorar.text
        assert "Qué cuesta hoy" not in detalle.text

    def test_la_cookie_de_vista_sigue_mandando_en_precios(self, client: TestClient, datos: None) -> None:
        client.cookies.set("vista", "mapa")

        assert 'id="ph-map-data"' in client.get("/precios").text

    def test_los_enlaces_internos_del_tablero_apuntan_a_precios(
        self, client: TestClient, datos: None
    ) -> None:
        for url in ("/precios", "/precios?vista=mapa", "/precios?vista=explorar&productos=arroz"):
            texto = client.get(url).text
            assert not re.search(r'(href|action|hx-get)="/\?', texto), url
            assert not re.search(r'(action|hx-get)="/"', texto), url

    def test_el_tablero_enlaza_a_la_landing(self, client: TestClient, datos: None) -> None:
        texto = client.get("/precios").text

        assert re.search(r'<a[^>]+href="/"[^>]*>', texto)
        assert "Qué es Guani" in texto


class TestLosEnlacesYaCompartidos:
    @pytest.mark.parametrize(
        "query",
        [
            "vista=tablero&provincia=la-habana",
            "producto=arroz",
            "productos=arroz,pollo&pestana=pollo&vista=explorar",
            "desde=2026-09-01&hasta=2026-09-10",
            "dias=90",
            "ver=cobertura",
            "capa=densidad",
            "vista=",
        ],
    )
    def test_la_raiz_con_parametros_de_la_home_redirige_a_precios(
        self, client: TestClient, query: str
    ) -> None:
        respuesta = client.get(f"/?{query}", follow_redirects=False)

        assert respuesta.status_code in (301, 302, 307, 308)
        assert respuesta.headers["location"] == f"/precios?{query}"

    def test_el_enlace_viejo_termina_en_la_vista_que_pedia(self, client: TestClient, datos: None) -> None:
        respuesta = client.get("/?vista=mapa&productos=arroz")

        assert respuesta.status_code == 200
        assert respuesta.url.path == "/precios"
        assert 'id="ph-map-data"' in respuesta.text

    def test_un_parametro_ajeno_no_redirige(self, client: TestClient) -> None:
        """`?fbclid=…` y compañía los agregan las redes al compartir: la landing
        se tiene que ver igual."""
        respuesta = client.get("/?fbclid=abc123", follow_redirects=False)

        assert respuesta.status_code == 200
        assert "Precios reales del mercado informal cubano." in respuesta.text


# ── «Así trabajo»: pase de capturas y tarjeta de video (2026-10-06) ──────────
#
# Los valores esperados (textos de los pasos, medidas, peso) salen del
# prototipo aprobado y de los archivos, escritos antes que `build_landing_flow`.

PASOS = (
    "Un emprendedor publica su oferta en un grupo o canal.",
    "El anuncio llega a Guani tal cual se escribió.",
    "Saco el producto, el precio y la provincia.",
    "Si hay dudas lo revisa una persona; si es claro, el sistema lo acepta y una persona lo audita después.",
    "Lo ves en el tablero como rango, mediana y número de anuncios.",
)
CAPTURAS = tuple(f"flujo-{n}.webp" for n in range(1, 6))


def _imagenes_del_pase(html: str) -> list[str]:
    return re.findall(r'<img\b[^>]*class="lp-shot"[^>]*>', html)


def _copiar_capturas(destino: Path, *, salvo: str | None = None) -> None:
    for nombre in CAPTURAS:
        if nombre != salvo:
            shutil.copy(landing_view.LANDING_STATIC_DIR / nombre, destino / nombre)


@pytest.fixture(autouse=True)
def _video_del_recorrido_encendido(monkeypatch: pytest.MonkeyPatch) -> None:
    """El video está retirado de la página (`FLOW_VIDEO_ENABLED = False`); estas
    pruebas siguen cubriendo cómo se arma y se dibuja cuando se vuelva a
    encender. `TestElVideoDelRecorridoEstaRetirado` prueba el valor real."""
    monkeypatch.setattr(landing_view, "FLOW_VIDEO_ENABLED", True, raising=False)


@pytest.fixture
def directorio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un directorio de estáticos de mentira, con las cinco capturas reales
    copiadas: cada caso le quita o le agrega lo que necesita sin tocar el repo."""
    _copiar_capturas(tmp_path)
    monkeypatch.setattr(landing_view, "LANDING_STATIC_DIR", tmp_path)
    return tmp_path


class TestElRecorridoArmado:
    def test_con_las_cinco_capturas_hay_cinco_pasos_en_orden(self) -> None:
        flujo = build_landing_flow()

        assert [paso.text for paso in flujo.steps] == list(PASOS)
        assert len(flujo.shots) == 5
        for numero, captura in enumerate(flujo.shots, start=1):
            assert captura.src.startswith(f"/static/landing/flujo-{numero}.webp?v=")
            assert (captura.width, captura.height) == (460, 958)
            assert len(captura.alt) > 20

    def test_las_medidas_declaradas_son_las_de_los_archivos(self) -> None:
        """`width`/`height` reservan el hueco antes de que la imagen llegue:
        si no coinciden con el archivo, la captura sale deformada."""
        image = pytest.importorskip("PIL.Image")

        for nombre, captura in zip(CAPTURAS, build_landing_flow().shots, strict=True):
            with image.open(landing_view.LANDING_STATIC_DIR / nombre) as archivo:
                assert archivo.size == (captura.width, captura.height)

    def test_las_capturas_pesan_poco(self) -> None:
        """Conexión lenta del visitante: el pase entero cabe en 150 KB."""
        total = sum((landing_view.LANDING_STATIC_DIR / n).stat().st_size for n in CAPTURAS)

        assert total < 150_000

    def test_si_falta_una_captura_no_hay_pase(self, tmp_path: Path) -> None:
        _copiar_capturas(tmp_path, salvo="flujo-3.webp")

        flujo = build_landing_flow(tmp_path)

        assert flujo.shots == ()
        assert [paso.text for paso in flujo.steps] == list(PASOS)

    def test_una_captura_vacia_cuenta_como_faltante(self, tmp_path: Path) -> None:
        _copiar_capturas(tmp_path, salvo="flujo-2.webp")
        (tmp_path / "flujo-2.webp").write_bytes(b"")

        assert build_landing_flow(tmp_path).shots == ()

    def test_sin_el_mp4_no_hay_video(self, tmp_path: Path) -> None:
        _copiar_capturas(tmp_path)

        assert build_landing_flow(tmp_path).video is None

    def test_el_video_del_repo_pesa_menos_de_un_mega_y_trae_su_portada(self) -> None:
        # El video se sirve a una conexión lenta: el tope es parte del
        # contrato, no un detalle. Si se vuelve a montar más pesado, este
        # caso obliga a decidirlo a propósito.
        video = build_landing_flow().video

        assert video is not None
        assert (landing_view.LANDING_STATIC_DIR / "guani-flujo.mp4").stat().st_size < 1_000_000
        assert video.poster is not None
        assert video.poster.startswith("/static/landing/guani-flujo-poster.webp?v=")

    def test_el_video_trae_su_peso_real_y_la_captura_5_de_portada(self, tmp_path: Path) -> None:
        _copiar_capturas(tmp_path)
        (tmp_path / "guani-flujo.mp4").write_bytes(b"\0" * 3_400_000)

        video = build_landing_flow(tmp_path).video

        assert video is not None
        assert video.src.startswith("/static/landing/guani-flujo.mp4?v=")
        assert video.size_text == "3,4 MB"
        assert video.poster is not None
        assert video.poster.startswith("/static/landing/flujo-5.webp?v=")

    def test_la_portada_propia_gana_a_la_captura_5(self, tmp_path: Path) -> None:
        _copiar_capturas(tmp_path)
        (tmp_path / "guani-flujo.mp4").write_bytes(b"\0" * 12_960_000)
        (tmp_path / "guani-flujo-poster.webp").write_bytes(b"RIFF")

        video = build_landing_flow(tmp_path).video

        assert video is not None
        assert video.size_text == "13,0 MB"
        assert video.poster is not None
        assert video.poster.startswith("/static/landing/guani-flujo-poster.webp?v=")

    def test_un_video_chico_no_se_anuncia_como_cero(self, tmp_path: Path) -> None:
        (tmp_path / "guani-flujo.mp4").write_bytes(b"\0" * 20_000)

        video = build_landing_flow(tmp_path).video

        assert video is not None
        assert video.size_text == "0,1 MB"
        assert video.poster is None

    def test_un_video_vacio_no_es_un_video(self, tmp_path: Path) -> None:
        (tmp_path / "guani-flujo.mp4").write_bytes(b"")

        assert build_landing_flow(tmp_path).video is None


class TestAsiTrabajoEnLaPagina:
    def test_trae_las_cinco_capturas_con_alt_y_medidas(self, client: TestClient) -> None:
        imagenes = _imagenes_del_pase(client.get("/").text)

        assert len(imagenes) == 5
        for numero, imagen in enumerate(imagenes, start=1):
            assert f'src="/static/landing/flujo-{numero}.webp?v=' in imagen
            assert re.search(r'alt="[^"]{20,}"', imagen)
            assert 'width="460"' in imagen
            assert 'height="958"' in imagen
            assert 'decoding="async"' in imagen

    def test_solo_la_primera_captura_carga_de_entrada(self, client: TestClient) -> None:
        imagenes = _imagenes_del_pase(client.get("/").text)

        assert 'loading="lazy"' not in imagenes[0]
        assert all('loading="lazy"' in imagen for imagen in imagenes[1:])

    def test_los_cinco_pasos_van_en_una_lista_ordenada(self, client: TestClient) -> None:
        listas = re.findall(r'<ol class="lp-flow-steps"[^>]*>.*?</ol>', client.get("/").text, flags=re.S)

        assert len(listas) == 1
        items = [" ".join(_visible(li).split()) for li in re.findall(r"<li\b.*?</li>", listas[0], flags=re.S)]
        assert items == [f"{n} {texto}" for n, texto in enumerate(PASOS, start=1)]

    def test_con_el_pase_no_van_las_tarjetas_de_respaldo(self, client: TestClient) -> None:
        texto = client.get("/").text

        assert 'class="lp-steps"' not in texto
        assert "Leo los anuncios" not in texto

    def test_cada_captura_se_sirve(self, client: TestClient) -> None:
        for imagen in _imagenes_del_pase(client.get("/").text):
            respuesta = client.get(re.search(r'src="([^"]+)"', imagen).group(1))  # type: ignore[union-attr]

            assert respuesta.status_code == 200
            assert respuesta.headers["content-type"] == "image/webp"
            assert respuesta.content[:4] == b"RIFF"

    def test_sin_el_mp4_no_hay_video(self, client: TestClient, directorio: Path) -> None:
        texto = client.get("/").text

        assert "<video" not in texto
        assert "Mira el recorrido completo" not in texto

    def test_el_pase_sigue_sin_javascript(self, client: TestClient) -> None:
        """«Así trabajo» no lleva guion. Desde landing-entradas la página tiene
        uno solo, el de los clips de «Entra por donde quieras», y va después."""
        html = client.get("/").text
        principal = html.split('<main class="lp-main">')[1]

        assert "<script" not in principal.split('id="entradas"')[0]
        assert principal.count("<script") == 1

    def test_si_falta_una_captura_no_hay_pase_pero_siguen_los_cuatro_pasos(
        self, client: TestClient, directorio: Path
    ) -> None:
        (directorio / "flujo-4.webp").unlink()

        texto = client.get("/").text

        assert _imagenes_del_pase(texto) == []
        assert 'class="lp-flow-steps"' not in texto
        assert 'class="lp-steps"' not in texto  # las tarjetas ilustradas de respaldo ya no existen
        assert len(re.findall(r'<li class="lp-card">', texto)) == 4

    def test_con_el_mp4_hay_video_con_boton_y_sin_autoplay(
        self, client: TestClient, directorio: Path
    ) -> None:
        (directorio / "guani-flujo.mp4").write_bytes(b"\0" * 3_400_000)

        texto = client.get("/").text
        videos = re.findall(r"<video\b[^>]*>", texto)

        assert len(videos) == 1
        assert 'preload="none"' in videos[0]
        assert "controls" in videos[0]
        assert "playsinline" in videos[0]
        assert "autoplay" not in videos[0]
        assert 'poster="/static/landing/flujo-5.webp?v=' in videos[0]
        assert 'src="/static/landing/guani-flujo.mp4?v=' in videos[0]
        # 735472e quitó a propósito el título y la nota de peso: el video va solo.
        visible = " ".join(_visible(texto).split())
        assert "Mira el recorrido completo" not in visible
        assert "solo se descarga si lo abres" not in visible

    def test_el_video_no_depende_del_pase(self, client: TestClient, directorio: Path) -> None:
        """Faltando una captura vuelven las tarjetas, pero el video que existe
        se ofrece igual."""
        (directorio / "flujo-1.webp").unlink()
        (directorio / "guani-flujo.mp4").write_bytes(b"\0" * 1_000_000)

        texto = client.get("/").text

        assert _imagenes_del_pase(texto) == []
        assert len(re.findall(r"<video\b", texto)) == 1


# ── «Entra por donde quieras»: tarjetas con el sistema real (landing-entradas) ─
#
# Los valores esperados salen del prototipo aprobado
# (`docs/prototipos/landing-entradas.html`) y de los archivos, escritos antes
# que `build_landing_entries`.

TARJETAS = (
    ("/precios?vista=tablero", "Precios de hoy", "tablero"),
    ("/precios?vista=mapa", "Mapa", "mapa"),
    ("/precios?vista=explorar", "Explorar", "explorar"),
    ("/datos.csv", "Datos (CSV)", None),
)
VISTAS_CON_CLIP = ("tablero", "mapa", "explorar")
#: Tope por clip: la conexión del visitante es lenta. Los cuatro medios juntos
#: (clips) tienen que caber en 600 KB.
TOPE_CLIP = 150_000
TOPE_CAPTURA = 60_000


def _copiar_entradas(destino: Path, *, salvo: str | None = None) -> None:
    for vista in VISTAS_CON_CLIP:
        for ext in ("mp4", "webp"):
            nombre = f"entradas-{vista}.{ext}"
            if nombre != salvo:
                shutil.copy(landing_view.LANDING_STATIC_DIR / nombre, destino / nombre)


class TestLasEntradasArmadas:
    def test_son_cuatro_en_el_orden_del_prototipo(self) -> None:
        entradas = build_landing_entries()

        assert [(e.href, e.title) for e in entradas] == [(h, t) for h, t, _ in TARJETAS]

    def test_el_tablero_el_mapa_y_explorar_traen_captura_y_clip(self) -> None:
        for entrada, (_, _, vista) in zip(build_landing_entries(), TARJETAS, strict=True):
            if vista is None:
                continue
            assert entrada.poster is not None
            assert entrada.poster.startswith(f"/static/landing/entradas-{vista}.webp?v=")
            assert entrada.clip is not None
            assert entrada.clip.startswith(f"/static/landing/entradas-{vista}.mp4?v=")

    def test_el_csv_no_tiene_pantalla_y_se_queda_con_el_icono(self) -> None:
        csv = build_landing_entries()[3]

        assert (csv.clip, csv.poster, csv.alt) == (None, None, None)
        assert csv.icon == "download"

    def test_cada_medio_trae_su_texto_alternativo(self) -> None:
        for entrada in build_landing_entries()[:3]:
            assert entrada.alt is not None
            assert len(entrada.alt) > 40

    def test_los_archivos_del_repo_existen_y_pesan_poco(self) -> None:
        """Un tope por clip y por captura: si se vuelve a grabar más pesado,
        este caso obliga a decidirlo a propósito."""
        carpeta = landing_view.LANDING_STATIC_DIR
        for vista in VISTAS_CON_CLIP:
            clip = carpeta / f"entradas-{vista}.mp4"
            captura = carpeta / f"entradas-{vista}.webp"
            assert 0 < clip.stat().st_size < TOPE_CLIP, clip.name
            assert 0 < captura.stat().st_size < TOPE_CAPTURA, captura.name

    def test_los_clips_no_llevan_metadatos_ni_audio(self) -> None:
        """Mudos y sin etiquetas: ninguna cadena de texto del encoder más allá
        de la que escribe el propio códec. Se mira que no haya pista de audio
        (`soun`) ni títulos, autores o rutas."""
        for vista in VISTAS_CON_CLIP:
            datos = (landing_view.LANDING_STATIC_DIR / f"entradas-{vista}.mp4").read_bytes()
            assert b"soun" not in datos
            assert b"/home/" not in datos
            assert b"\xa9nam" not in datos and b"\xa9ART" not in datos

    def test_las_capturas_no_llevan_metadatos_de_la_camara_ni_del_sistema(self) -> None:
        for vista in VISTAS_CON_CLIP:
            datos = (landing_view.LANDING_STATIC_DIR / f"entradas-{vista}.webp").read_bytes()
            assert b"EXIF" not in datos
            assert b"/home/" not in datos

    def test_sin_el_clip_no_hay_medio_y_la_tarjeta_vuelve_al_icono(self, tmp_path: Path) -> None:
        _copiar_entradas(tmp_path, salvo="entradas-mapa.mp4")

        mapa = build_landing_entries(tmp_path)[1]

        assert (mapa.clip, mapa.poster) == (None, None)
        assert mapa.icon == "map"

    def test_sin_la_captura_tampoco(self, tmp_path: Path) -> None:
        _copiar_entradas(tmp_path, salvo="entradas-explorar.webp")

        assert build_landing_entries(tmp_path)[2].poster is None

    def test_un_archivo_vacio_cuenta_como_faltante(self, tmp_path: Path) -> None:
        _copiar_entradas(tmp_path, salvo="entradas-tablero.mp4")
        (tmp_path / "entradas-tablero.mp4").write_bytes(b"")

        assert build_landing_entries(tmp_path)[0].clip is None


class TestLaSeccionRenderizada:
    def _seccion(self, client: TestClient) -> str:
        html = client.get("/").text
        return html.split('id="entradas"')[1].split("</section>")[0]

    def test_cada_tarjeta_sigue_siendo_un_enlace_a_su_vista(self, client: TestClient) -> None:
        seccion = self._seccion(client)

        for href, titulo, _ in TARJETAS:
            assert re.search(rf'<a class="lp-tile[^"]*" href="{re.escape(href)}"', seccion)
            assert titulo in seccion

    def test_tres_tarjetas_traen_poster_y_clip_sin_bajarlo(self, client: TestClient) -> None:
        seccion = self._seccion(client)
        videos = re.findall(r"<video\b[^>]*>", seccion)

        assert len(videos) == 3
        for video, vista in zip(videos, VISTAS_CON_CLIP, strict=True):
            assert f'poster="/static/landing/entradas-{vista}.webp?v=' in video
            assert f'data-clip="/static/landing/entradas-{vista}.mp4?v=' in video
            assert 'preload="none"' in video
            assert " muted" in video and " loop" in video and " playsinline" in video
            # Sin JS no se baja nada: el clip va en `data-clip`, no en `src`.
            assert " src=" not in video
            assert "autoplay" not in video

    def test_el_clip_no_es_el_unico_portador_de_informacion(self, client: TestClient) -> None:
        seccion = self._seccion(client)

        assert seccion.count('role="img"') == 3
        assert seccion.count('aria-hidden="true"') >= 3  # los tres videos, más el ícono
        for texto in ("El tablero completo.", "Provincia por provincia.", "Cómo se movió cada precio."):
            assert texto in seccion

    def test_el_csv_lleva_el_icono_y_ninguna_captura(self, client: TestClient) -> None:
        csv = self._seccion(client).split('href="/datos.csv"')[1].split("</a>")[0]

        assert "download" in csv
        assert "<video" not in csv and "<img" not in csv

    def test_el_script_respeta_movimiento_reducido_ahorro_y_2g(self, client: TestClient) -> None:
        html = client.get("/").text
        guion = html.split("<script>")[-1].split("</script>")[0]

        assert 'matchMedia("(prefers-reduced-motion: reduce)")' in guion
        assert "saveData" in guion and "2g" in guion
        assert "IntersectionObserver" in guion

    def test_sin_los_archivos_la_seccion_vuelve_a_las_tarjetas_de_icono(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(landing_view, "LANDING_STATIC_DIR", tmp_path)

        seccion = self._seccion(client)

        assert "<video" not in seccion
        assert seccion.count('class="m3-icon"') == 4

    def test_ni_la_seccion_ni_los_textos_traen_ids_ni_emisores(self, client: TestClient) -> None:
        seccion = self._seccion(client)

        assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", seccion)
        assert "wa.me" not in seccion and "@c.us" not in seccion

    def test_tuteo_nunca_voseo(self, client: TestClient) -> None:
        texto = _visible(self._seccion(client)).lower()

        for voseo in ("mirá", "elegí", "tocá", "entrá", "podés", "tenés"):
            assert voseo not in texto


# ── Narrativa (landing-narrativa, `docs/propuesta-narrativa-landing.md`) ──────
#
# El texto esperado es el de la propuesta aprobada, escrito antes que la
# plantilla. Lo que se prueba es doble: que diga lo nuevo y que ya no diga lo
# que dejó de ser cierto (el carril 0 descarta solo y el carril 1 acepta solo).

PAGINAS_PUBLICAS = (
    "/",
    "/preguntas",
    "/terminos",
    "/privacidad",
    "/metodologia",
    "/precios",
    "/precios?vista=mapa",
    "/precios?vista=explorar",
)
LO_QUE_YA_NO_ES_CIERTO = (
    "uno por uno",
    "Ninguna cifra de esta página es una estimación",
    "ninguno cuenta hasta que una persona lo revisa",
    "Una persona revisa cada precio",
)


class TestLaNarrativaEsCierta:
    def test_la_promesa_dice_que_es_una_herramienta_asistida(self, client: TestClient) -> None:
        texto = _visible(client.get("/").text)

        assert "Precios reales del mercado informal cubano." in texto
        assert (
            "Guani lee los anuncios donde ya se vende, los pasa a una misma unidad y te los "
            "enseña con su cuenta. Una máquina lee; una persona supervisa."
        ) in texto
        assert "Hola, soy Guani" not in texto

    @pytest.mark.parametrize("ruta", PAGINAS_PUBLICAS)
    def test_ninguna_pagina_publica_dice_lo_que_dejo_de_ser_cierto(
        self, client: TestClient, ruta: str
    ) -> None:
        respuesta = client.get(ruta)
        assert respuesta.status_code == 200, ruta
        texto = " ".join(_visible(respuesta.text).split())

        for frase in LO_QUE_YA_NO_ES_CIERTO:
            assert frase not in texto, f"{ruta}: «{frase}»"

    def test_la_tarjeta_de_confianza_dice_quien_reviso(self, client: TestClient) -> None:
        seccion = client.get("/").text.split('id="lp-why"')[1].split("</section>")[0]

        assert (
            "Al lado de cada cifra va cuántos anuncios revisó una persona y cuántos aceptó el "
            "sistema solo. Y una persona audita lo que el sistema aceptó."
        ) in " ".join(_visible(seccion).split())

    def test_el_pie_dice_de_cuantos_anuncios_sale_cada_cifra(self, client: TestClient) -> None:
        for ruta in ("/precios?vista=mapa",):  # el tablero y Explorar apagan este pie
            texto = " ".join(_visible(client.get(ruta).text).split())

            assert (
                "Cada cifra dice de cuántos anuncios sale y cuántos revisó una persona. "
                "Es una muestra en formación, no un censo."
            ) in texto, ruta

    def test_las_preguntas_y_los_terminos_dicen_lo_mismo_que_la_metodologia(self, client: TestClient) -> None:
        preguntas = " ".join(_visible(client.get("/preguntas").text).split())
        terminos = " ".join(_visible(client.get("/terminos").text).split())

        assert "cuando una persona lo revisa o el sistema lo acepta con auditoría" in preguntas
        assert 'href="/metodologia"' in client.get("/preguntas").text
        assert (
            "Los anuncios los revisa una persona o los acepta el sistema con auditoría, "
            "pero aun así puede haber errores"
        ) in terminos


class TestElVideoDelRecorridoEstaRetirado:
    """La voz de `guani-flujo.mp4` cuenta el recorrido viejo (una persona revisa
    todo). El archivo queda en el repo; la página no lo enseña hasta regrabarlo."""

    def test_por_defecto_la_pagina_no_lo_muestra(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.undo()  # la fixture de módulo lo enciende para probar el resto

        assert landing_view.FLOW_VIDEO_ENABLED is False
        assert build_landing_flow().video is None
        assert "<video" not in client.get("/").text.split('id="entradas"')[0]
        assert (landing_view.LANDING_STATIC_DIR / "guani-flujo.mp4").is_file()


# ── Cuatro pasos y «Para quién es» (prototipo aprobado, landing-narrativa) ────
#
# Los textos son los de `docs/propuesta-narrativa-landing.md` y el prototipo
# `docs/prototipos/landing-narrativa.html`, escritos antes que la plantilla.

CUATRO_PASOS = (
    ("Escucha", "Lee los grupos y canales donde ya se vende. Nadie tiene que escribirle nada."),
    (
        "Entiende",
        "Un asistente saca producto, precio, cantidad y provincia, y lo lleva todo a la misma "
        "unidad y moneda.",
    ),
    (
        "Filtra",
        "Aparta lo que no es un anuncio de precio y frena lo dudoso. Lo difícil lo decide una "
        "persona; lo claro, el sistema lo acepta y una persona lo audita después.",
    ),
    (
        "Te lo enseña con su cuenta",
        "Rango, mediana y cuántos anuncios hay detrás, separando los revisados a mano de los "
        "aceptados por el sistema.",
    ),
)
PARA_QUIEN = (
    ("/precios?vista=tablero", "Si compras o vendes", "¿Cuánto cuesta esto hoy en mi provincia?"),
    ("/precios?vista=explorar", "Si revendes o decides", "Qué se mueve y dónde."),
    ("/datos.csv", "Si investigas o informas", "Los datos en CSV, con su metodología."),
)


def _items(html: str, clase: str) -> list[str]:
    lista = re.search(rf'<(?:ol|ul) class="{clase}"[^>]*>.*?</(?:ol|ul)>', html, flags=re.S)
    assert lista is not None, clase
    return [" ".join(_visible(li).split()) for li in re.findall(r"<li\b.*?</li>", lista.group(0), flags=re.S)]


class TestLosCuatroPasos:
    def test_son_cuatro_en_orden_con_el_texto_aprobado(self, client: TestClient) -> None:
        assert _items(client.get("/").text, "lp-steps4") == [
            f"{n} {titulo} {texto}" for n, (titulo, texto) in enumerate(CUATRO_PASOS, start=1)
        ]

    def test_la_seccion_abre_diciendo_quien_hace_que(self, client: TestClient) -> None:
        seccion = client.get("/").text.split('id="lp-work"')[1].split("</section>")[0]

        assert "Una máquina lee los anuncios y una persona supervisa." in " ".join(_visible(seccion).split())

    def test_el_pase_de_capturas_se_rotula_como_un_ejemplo(self, client: TestClient) -> None:
        seccion = client.get("/").text.split('id="lp-work"')[1].split("</section>")[0]

        assert "Así pasó con un anuncio real" in _visible(seccion)
        assert seccion.index('class="lp-steps4"') < seccion.index('class="lp-flow"')

    def test_sin_las_capturas_no_se_rotula_ningun_ejemplo(self, client: TestClient, directorio: Path) -> None:
        (directorio / "flujo-2.webp").unlink()

        assert "Así pasó con un anuncio real" not in _visible(client.get("/").text)


class TestParaQuienEs:
    def _seccion(self, client: TestClient) -> str:
        return client.get("/").text.split('id="lp-who"')[1].split("</section>")[0]

    def test_son_tres_enlaces_con_su_titulo_y_su_pregunta(self, client: TestClient) -> None:
        seccion = self._seccion(client)

        assert "Para quién es" in _visible(seccion)
        sin_iconos = re.sub(r'<span class="m3-icon"[^>]*>[^<]*</span>', "", seccion)
        assert _items(sin_iconos, "lp-who") == [f"{titulo} {texto}" for _, titulo, texto in PARA_QUIEN]
        enlaces = re.findall(r'<a class="lp-who-link[^"]*" href="([^"]+)"', seccion)
        assert enlaces == [href for href, _, _ in PARA_QUIEN]

    def test_cada_enlace_lleva_a_una_ruta_que_existe(self, client: TestClient) -> None:
        for href, _, _ in PARA_QUIEN:
            assert client.get(href).status_code == 200, href

    def test_los_iconos_son_decorativos(self, client: TestClient) -> None:
        seccion = self._seccion(client)

        assert seccion.count('class="m3-icon" aria-hidden="true"') == 3


class TestElOrdenDeLaPagina:
    def test_promesa_precio_recorrido_confianza_para_quien_y_entradas(self, client: TestClient) -> None:
        html = client.get("/").text
        orden = [
            '<h1 class="lp-h1">',
            'id="lp-work"',
            'id="lp-why"',
            'id="lp-who"',
            'id="entradas"',
        ]
        posiciones = [html.index(marca) for marca in orden]

        assert posiciones == sorted(posiciones)


# ── «El atardecer cubano» (landing-estetica, prototipo aprobado) ──────────────
#
# El héroe es un atardecer con el teléfono del tablero real; «Así trabajo» es la
# noche. Lo que se prueba es la estructura y lo que no se puede romper: la
# captura se declara con su medida, lo decorativo no entra al árbol de
# accesibilidad y el sol no se anima con movimiento reducido. Los colores viven
# en `design_tokens.css` (`test_static_assets` lo vigila).


def _heroe(html: str) -> str:
    return html.split('<header class="lp-hero">')[1].split("</header>")[0]


class TestElHeroeEsUnAtardecer:
    def test_trae_el_sol_a_guani_la_ciudad_y_el_muro(self, client: TestClient) -> None:
        heroe = _heroe(client.get("/").text)

        for clase in ("lp-sun", "lp-hero-avatar", "lp-city-back", "lp-wall"):
            assert f'class="{clase}' in heroe, clase
        assert heroe.count('class="lp-palm ') == 2

    def test_guani_va_grande_sobre_el_sol_y_ya_no_hay_telefono(self, client: TestClient) -> None:
        """El teléfono con el tablero salió del héroe: el protagonista es Guani,
        sobre el sol. El héroe ya no lleva ninguna imagen."""
        heroe = _heroe(client.get("/").text)
        escenario = heroe.split('class="lp-stage"')[1]

        assert escenario.index('class="lp-sun"') < escenario.index('class="lp-hero-avatar"')
        assert "lp-hero-phone" not in heroe
        assert "<img" not in heroe
        assert "entradas-tablero" not in heroe

    def test_la_ciudad_las_palmas_el_sol_y_guani_son_decorativos(self, client: TestClient) -> None:
        heroe = _heroe(client.get("/").text)

        for clase in ("lp-city-back", "lp-wall", "lp-palm lp-palm-l", "lp-palm lp-palm-r"):
            assert re.search(rf'<svg class="{clase}"[^>]*aria-hidden="true"', heroe), clase
        assert re.search(r'<div class="lp-sun"[^>]*aria-hidden="true"', heroe)
        assert 'class="lp-hero-avatar" aria-hidden="true"' in heroe

    def test_guani_esta_en_el_heroe_y_tambien_en_el_pie(self, client: TestClient) -> None:
        html = client.get("/").text

        assert _heroe(html).count("ph-brand-avatar") == 1
        assert "ph-brand-avatar" in html.split("</main>")[1]

    def test_los_dos_avatares_no_repiten_el_id_del_recorte(self, client: TestClient) -> None:
        ids = re.findall(r'<clipPath id="([^"]+)"', client.get("/").text)

        assert len(ids) == 2
        assert len(set(ids)) == 2

    def test_las_burbujas_siguen_diciendo_el_rango_la_provincia_y_la_muestra(
        self, client: TestClient, datos: None
    ) -> None:
        assert len(re.findall(r'<div class="lp-globe', _heroe(client.get("/").text))) == 3

    def test_el_sol_no_se_anima_con_movimiento_reducido(self, client: TestClient) -> None:
        html = client.get("/").text
        reducido = html.split("@media (prefers-reduced-motion: reduce)")[1].split("}\n  }")[0]

        assert ".lp-sun" in reducido and "animation: none" in reducido

    def test_ya_no_existe_el_armado_del_telefono(self) -> None:
        assert not hasattr(landing_view, "build_hero_phone")


class TestLaNocheDelRecorrido:
    def test_asi_trabajo_es_la_banda_de_noche(self, client: TestClient) -> None:
        html = client.get("/").text

        assert re.search(r'<section class="[^"]*\blp-night\b[^"]*" id="lp-work"', html)

    def test_los_cuatro_pasos_siguen_siendo_los_mismos(self, client: TestClient) -> None:
        assert _items(client.get("/").text, "lp-steps4") == [
            f"{n} {titulo} {texto}" for n, (titulo, texto) in enumerate(CUATRO_PASOS, start=1)
        ]


# ── El día completo (landing-estetica, prototipo `landing-dia-completo.html`) ─
#
# La página es un día: atardecer (portada), noche (el precio y el recorrido),
# amanecer (por qué puedes fiarte), día (para quién, preguntas, entradas) y
# ocaso (justo antes del pie). Cada hora es un envoltorio con su fondo; lo que
# se prueba es qué contiene cada uno y en qué orden, no el color (los colores
# son tokens y `test_static_assets` los vigila).


def _envoltorio(html: str, clase: str) -> str:
    """El contenido de `<div class="<clase>">…</div>` hasta su cierre, contando
    los `<div` anidados."""
    inicio = html.index(f'<div class="{clase}">')
    profundidad, pos = 0, inicio
    for m in re.finditer(r"<div\b|</div>", html[inicio:]):
        profundidad += 1 if m.group(0) == "<div" else -1
        pos = inicio + m.end()
        if profundidad == 0:
            break
    return html[inicio:pos]


class TestLasHorasDelDia:
    def test_el_recorrido_sigue_al_heroe_sin_ninguna_seccion_en_medio(self, client: TestClient) -> None:
        """«Así se lee un precio» se quitó (no aportaba): la noche arranca donde
        termina el muro de la portada."""
        html = client.get("/").text
        entre = html.split("</header>")[1].split('<section class="lp-night"')[0]

        assert "<section" not in entre
        assert 'id="lp-price"' not in html
        assert "Así se lee un precio" not in html
        assert "lp-sea" not in html
        for clase in ("lp-dot", "lp-median", "lp-range", "lp-legend"):
            assert f'class="{clase}' not in html, clase

    def test_por_que_puedes_fiarte_es_el_amanecer(self, client: TestClient) -> None:
        html = client.get("/").text
        amanecer = _envoltorio(html, "lp-dawn")

        assert 'id="lp-why"' in amanecer
        assert 'id="lp-who"' not in amanecer
        assert html.index('id="lp-work"') < html.index('class="lp-dawn"')

    def test_para_quien_las_preguntas_y_las_entradas_son_el_dia(self, client: TestClient) -> None:
        html = client.get("/").text
        dia = _envoltorio(html, "lp-day")

        for marca in ('id="lp-who"', 'aria-label="Preguntas frecuentes"', 'id="entradas"'):
            assert marca in dia, marca
        assert 'id="lp-why"' not in dia
        assert html.index('class="lp-dawn"') < html.index('class="lp-day"')

    def test_el_ocaso_cierra_el_dia_justo_antes_del_pie(self, client: TestClient) -> None:
        html = client.get("/").text

        assert html.index('id="entradas"') < html.index('class="lp-ocaso"') < html.index("<footer")
        assert html.index("</main>") < html.index('class="lp-ocaso"')

    def test_el_ocaso_es_decorativo_y_reusa_las_palmas_de_la_portada(self, client: TestClient) -> None:
        html = client.get("/").text
        ocaso = html.split('<div class="lp-ocaso"')[1].split("<footer")[0]

        assert re.match(r'[^>]*aria-hidden="true"', ocaso)
        assert ocaso.count('class="lp-ocaso-palm ') == 2
        assert ocaso.count('href="#palma"') == 2
        assert 'class="lp-ocaso-city"' in ocaso
        # Las palmas se definen una sola vez, en la portada.
        assert html.count('id="palma"') == 1

    def test_no_hay_ningun_envoltorio_vacio(self, client: TestClient) -> None:
        html = client.get("/").text

        for clase in ("lp-dawn", "lp-day"):
            assert "<section" in _envoltorio(html, clase), clase

    def test_los_tokens_nuevos_del_dia_existen(self) -> None:
        fuente = (Path(landing_view.__file__).parent / "design_tokens.css").read_text(encoding="utf-8")

        assert "--ph-dusk-manana:" in fuente
        assert "--ph-dusk-sombra-dia:" in fuente


# ── Animaciones (landing-estetica, plan aprobado) ─────────────────────────────
#
# Todo es CSS: ningún guion nuevo, y solo se mueven propiedades que no
# recalculan el diseño. Lo que se prueba es lo que se puede romper sin que se
# note en pantalla: que cada animación exista, que no toque el diseño, que lo
# atado al scroll solo corra donde el navegador lo soporta y que el movimiento
# reducido lo apague todo.

NUEVAS = (
    "lp-halo",
    "lp-guani-sube",
    "lp-burbuja",
    "lp-foco",
    "lp-brillo",
    "lp-brisa",
    "lp-titileo",
    "lp-paralaje",
    "lp-estrellas",
    "lp-sol-se-pone",
    "lp-chispa",
)
#: Propiedades que se animan sin recalcular el diseño ni repintar el texto
#: alrededor: el compositor las resuelve solo.
BARATAS = {"opacity", "transform", "filter", "rotate", "translate", "scale"}


def _css_de_la_landing(html: str) -> str:
    bloques = re.findall(r"<style>(.*?)</style>", html, flags=re.S)
    return "\n".join(b for b in bloques if "lp-hero" in b)


def _cuerpo_de_keyframes(css: str, nombre: str) -> str:
    inicio = css.index(
        f"@keyframes {nombre} " if f"@keyframes {nombre} " in css else f"@keyframes {nombre}{{"
    )
    profundidad, pos = 0, css.index("{", inicio)
    fin = pos
    for i, c in enumerate(css[pos:], start=pos):
        profundidad += c == "{"
        profundidad -= c == "}"
        if profundidad == 0:
            fin = i
            break
    return css[pos : fin + 1]


class TestLasAnimaciones:
    def test_cada_animacion_del_plan_esta_definida_y_se_usa(self, client: TestClient) -> None:
        css = _css_de_la_landing(client.get("/").text)

        for nombre in NUEVAS:
            assert f"@keyframes {nombre}" in css, f"falta @keyframes {nombre}"
            assert re.search(rf"animation:[^;]*\b{nombre}\b", css), f"{nombre} no se usa"

    def test_solo_se_animan_propiedades_baratas(self, client: TestClient) -> None:
        css = _css_de_la_landing(client.get("/").text)

        for nombre in NUEVAS:
            cuerpo = _cuerpo_de_keyframes(css, nombre)
            propiedades = set(re.findall(r"([a-z-]+)\s*:", re.sub(r"\d+%|from|to", "", cuerpo)))
            assert propiedades <= BARATAS, f"{nombre} anima {sorted(propiedades - BARATAS)}"

    def test_lo_atado_al_scroll_solo_corre_donde_el_navegador_lo_soporta(self, client: TestClient) -> None:
        css = _css_de_la_landing(client.get("/").text)

        usos = [m.start() for m in re.finditer(r"(?<!\()animation-timeline\s*:", css)]
        assert usos, "no hay ninguna animación atada al scroll"
        for pos in usos:
            antes = css[:pos]
            ultimo = antes.rindex("@supports")
            assert antes[ultimo:].startswith("@supports (animation-timeline"), pos
            # y ese bloque no se cerró antes de llegar acá
            bloque = css[ultimo:pos]
            assert bloque.count("{") > bloque.count("}"), pos

    def test_las_palmas_se_mecen_con_rotate_y_no_pisan_el_espejo(self, client: TestClient) -> None:
        css = _css_de_la_landing(client.get("/").text)

        assert "rotate:" in _cuerpo_de_keyframes(css, "lp-brisa")
        assert "transform" not in _cuerpo_de_keyframes(css, "lp-brisa")
        assert ".lp-palm-r { right: -52px; transform: scale(-1, 1); }" in css

    def test_las_burbujas_entran_una_tras_otra(self, client: TestClient) -> None:
        css = _css_de_la_landing(client.get("/").text)
        retrasos = [
            float(re.search(rf"\.lp-globe-{n}\s*\{{[^}}]*animation-delay:\s*([\d.]+)s", css).group(1))  # type: ignore[union-attr]
            for n in (1, 2, 3)
        ]

        assert retrasos == sorted(retrasos)
        assert len(set(retrasos)) == 3

    def test_el_movimiento_reducido_lo_apaga_todo(self, client: TestClient) -> None:
        css = _css_de_la_landing(client.get("/").text)
        reducido = css.split("@media (prefers-reduced-motion: reduce)")[-1]

        assert ".lp *, .lp *::before, .lp *::after" in reducido
        assert "animation: none !important" in reducido

    def test_el_destello_y_el_brillo_usan_un_token_no_un_color_a_mano(self, client: TestClient) -> None:
        css = _css_de_la_landing(client.get("/").text)

        assert "var(--ph-dusk-brillo)" in css
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", css)

    def test_siguen_siendo_un_solo_guion_y_es_el_de_los_clips(self, client: TestClient) -> None:
        html = client.get("/").text
        principal = html.split('<main class="lp-main">')[1]

        assert principal.count("<script") == 1
        assert "IntersectionObserver" in principal.split("<script>")[1]
