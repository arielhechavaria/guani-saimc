"""Landing pública en `/` y mudanza del tablero a `/precios` (2026-10-05).

Lo que estos casos protegen:

- `/` presenta a Guani y lleva a `/precios`; no sirve ni una cifra inventada ni
  un marcador del prototipo («000», «[PROVINCIA]», «Cifras de ejemplo»).
- `/precios` es la home de siempre, con los mismos parámetros.
- Un enlace ya compartido (`/?vista=…`, `/?producto=…`) sigue llegando a donde
  llegaba: redirige a `/precios` con el query string intacto.
- La tarjeta «Así se lee un precio» sale de anuncios reales: el titular es el
  rango, la mediana va adentro y el número de anuncios queda a la vista.
- Los enlaces de redes y contacto solo se dibujan si están configurados.

Los valores esperados de `landing_view` están escritos a mano a partir de las
observaciones de cada caso, antes de que existiera el módulo. Las fechas van
relativas a hoy, como en `test_public_precio.py`.
"""

import re
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

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
from saimc.web.app import app
from saimc.web.deps import get_db
from saimc.web.landing_view import build_footer_links, build_landing_price
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

    def test_un_punto_por_anuncio_entre_los_extremos_de_la_barra(self) -> None:
        obs = [_obs("Arroz", "La Habana", v) for v in ("280", "300", "320")]

        precio = build_landing_price(obs)

        assert precio is not None
        izquierdas = [p.left for p in precio.dots]
        assert len(izquierdas) == 3
        # El más barato cae donde empieza la barra y el más caro donde termina.
        assert min(izquierdas) == precio.bar_left
        assert max(izquierdas) == precio.bar_left + precio.bar_width
        # 300 está justo a mitad de camino entre 280 y 320.
        assert precio.median_left == precio.bar_left + precio.bar_width / 2

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
        assert "Hola, soy Guani." in respuesta.text
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
        assert "Hola, soy Guani." in respuesta.text

    def test_la_tarjeta_de_precio_muestra_el_rango_real_con_su_muestra(
        self, client: TestClient, datos: None
    ) -> None:
        texto = client.get("/").text
        tarjeta = texto.split('id="lp-price"')[1].split("</section>")[0]

        assert "Arroz · La Habana" in tarjeta
        assert re.search(r"280\s*–\s*320", tarjeta)
        assert "CUP/lb" in tarjeta
        # La mediana va dentro de la tarjeta del rango, y la muestra a la vista.
        assert "300" in tarjeta
        assert re.search(r"<strong[^>]*>3</strong>\s*anuncios", tarjeta)
        assert "uestra en formación" in tarjeta
        assert tarjeta.count("lp-dot") == 3

    def test_no_juzga_el_precio(self, client: TestClient, datos: None) -> None:
        tarjeta = client.get("/").text.split('id="lp-price"')[1].split("</section>")[0]

        for palabra in ("barato", "caro", "conviene", "oferta"):
            assert palabra not in tarjeta.lower()

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

        assert f"Guani · {date.today().year}" in texto
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


class TestLaLandingSinDatos:
    def test_con_la_base_vacia_responde_sin_inventar_cifras(self, client: TestClient) -> None:
        respuesta = client.get("/")

        assert respuesta.status_code == 200
        assert "Hola, soy Guani." in respuesta.text
        for marcador in MARCADORES:
            assert marcador not in _visible(respuesta.text)

    def test_con_la_base_vacia_la_tarjeta_no_trae_numeros(self, client: TestClient) -> None:
        tarjeta = client.get("/").text.split('id="lp-price"')[1].split("</section>")[0]
        visible = re.sub(r"<[^>]+>", " ", tarjeta)

        assert not re.search(r"\d", visible)
        assert "Cada punto es un anuncio" in visible

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

        assert 'class="lp-foot-links"' not in texto
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
        assert "Hola, soy Guani." not in respuesta.text

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
        assert "Hola, soy Guani." in respuesta.text
