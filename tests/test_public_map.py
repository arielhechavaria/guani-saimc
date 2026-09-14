"""Mapa público: geometría, cobertura y la primera ruta sin operador.

Lo que estos casos protegen no es cómo se ve el mapa —eso se mira, y desde
el 2026-09-12 el color de cada provincia lo decide JS del lado del cliente
(Leaflet), fuera del alcance de pytest— sino lo que Python sí controla: que
los nombres de la geometría sigan coincidiendo con los de la tabla
`provinces`, que una provincia sin dato aparezca igual en vez de
desaparecer, que el JSON que el JS consume tenga los 16 nombres con su
estado correcto, y que la vista pública no filtre la herramienta del
operador.

**Lo que dejó de poder probarse acá** (mapa Leaflet, docs/decisions.md
2026-09-12): qué color pinta cada polígono, si la capa de densidad se
aplica, si una provincia elegida se resalta sobre las demás — todo eso
corre en `estiloDeProvincia()` del script del pie, que pytest no ejecuta.
Lo que sigue cubierto es el contrato de datos que ese script consume
(`#ph-map-data`, `data-provincia`, `data-capa`): si ese contrato se rompe,
el JS falla en silencio y esto se pone rojo antes de que alguien lo note
navegando.
"""

import importlib.util
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType

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
from saimc.web.map_view import build_map


def _cargar_modulo_migracion(nombre: str) -> ModuleType:
    """Mismo patrón que `tests/test_catalog.py` y `tests/test_processing_layer.py`."""
    ruta = Path(__file__).resolve().parent.parent / "alembic" / "versions" / nombre
    spec = importlib.util.spec_from_file_location(nombre.removesuffix(".py"), ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _nombres_sembrados() -> set[str]:
    migracion = _cargar_modulo_migracion("0003_seed_provinces.py")
    return {name for _code, name in migracion._PROVINCE_CODES}


def _geojson() -> dict:
    ruta = (
        Path(__file__).resolve().parent.parent
        / "src" / "saimc" / "web" / "static" / "cuba_provincias.geojson"
    )
    return json.loads(ruta.read_text(encoding="utf-8"))


def _map_data(texto: str) -> list[dict]:
    """El `map_provinces_json` que `routers/public.py` deja en
    `#ph-map-data`: una fila por provincia, lo que el JS lee para colorear
    y armar el clic. No es HTML que haya que buscar por clase — es JSON."""
    bloque = texto.split('id="ph-map-data">')[1].split("</script>")[0]
    return json.loads(bloque)


def _seed_provincias(session: Session) -> None:
    """Las 16 provincias reales, sin ningún precio. `build_map` (desde el
    2026-09-12) saca los nombres de la tabla `provinces`, no de la
    geometría —al revés que hasta el 2026-09-11—, así que un test que
    quiera ver las 16 en `#ph-map-data` tiene que sembrarlas: el fixture
    `session` arranca con el esquema vacío, sin la migración
    `0003_seed_provinces.py` que sí corre en producción."""
    for i, nombre in enumerate(sorted(_nombres_sembrados())):
        session.add(Province(code=f"p{i:02d}", name=nombre))
    session.commit()


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class TestGeometria:
    """La geometría real ya no vive en el repo como módulo Python
    (`map_cuba.py` se borró, 2026-09-12, con el mapa SVG que la usaba):
    vive en `static/cuba_provincias.geojson`, lon/lat de Natural Earth, que
    Leaflet pide aparte (`tools/build_cuba_geojson.py`)."""

    def test_estan_las_dieciseis_provincias(self) -> None:
        assert len(_geojson()["features"]) == 16

    def test_los_nombres_son_los_de_la_tabla_provinces(self) -> None:
        """El cruce entre geometría y dato se hace por nombre, sin tabla de
        traducción en el medio. Natural Earth trae «Ciudad de la Habana», el
        nombre anterior a la reorganización de 2011, y
        `tools/build_cuba_geojson.py` (vía `build_cuba_map.load_cuba`) lo
        mapea; si esa corrección se pierde al regenerar, La Habana queda
        muda y nada más avisaría."""
        nombres = {f["properties"]["name"] for f in _geojson()["features"]}

        assert nombres == _nombres_sembrados()

    def test_cada_feature_tiene_slug_y_geometria_valida(self) -> None:
        for feature in _geojson()["features"]:
            assert feature["properties"]["slug"]
            assert feature["geometry"]["type"] == "MultiPolygon"
            polygons = feature["geometry"]["coordinates"]
            assert len(polygons) >= 1
            for polygon in polygons:
                anillo = polygon[0]
                # Un anillo cerrado (primer punto == último) con al menos
                # tres vértices distintos encierra área.
                assert anillo[0] == anillo[-1]
                assert len(anillo) >= 4


class TestBuildMap:
    def test_sin_datos_las_dieciseis_entran_en_cero(self) -> None:
        """El cero es el dato: dice dónde no se está mirando. Una provincia sin
        observaciones tiene que aparecer igual, no desaparecer del mapa."""
        vista = build_map(_nombres_sembrados(), {})

        assert len(vista.provinces) == 16
        assert vista.covered == 0
        assert vista.total_rows == 0
        assert all(not p.has_data for p in vista.provinces)
        assert all(p.state == "sin-datos" for p in vista.provinces)

    def test_cruza_los_conteos_con_la_geometria(self) -> None:
        vista = build_map(
            _nombres_sembrados(), {"La Habana": 113, "Santiago de Cuba": 105}
        )
        por_nombre = {p.name: p for p in vista.provinces}

        assert vista.covered == 2
        assert vista.total_rows == 218
        assert por_nombre["La Habana"].rows == 113
        assert por_nombre["Camagüey"].rows == 0
        assert por_nombre["Camagüey"].state == "sin-datos"


class TestDensidadDeAnuncios:
    """Capa «densidad» del mapa (2026-09-11): relativa al máximo actual, nunca
    un umbral fijo —§12.6 prohíbe inventar uno, y esto no decide qué se
    publica, solo cómo se sombrea."""

    def test_sin_ninguna_observacion_todas_quedan_en_cero(self) -> None:
        vista = build_map(_nombres_sembrados(), {})

        assert all(p.density_ratio == 0.0 for p in vista.provinces)

    def test_la_que_mas_tiene_pinta_a_fondo(self) -> None:
        vista = build_map(
            _nombres_sembrados(), {"La Habana": 113, "Santiago de Cuba": 105}
        )
        por_nombre = {p.name: p for p in vista.provinces}

        assert por_nombre["La Habana"].density_ratio == 1.0
        assert por_nombre["Santiago de Cuba"].density_ratio == pytest.approx(
            105 / 113
        )
        assert por_nombre["Camagüey"].density_ratio == 0.0

    def test_una_sola_observacion_en_una_provincia_esa_pinta_a_fondo(self) -> None:
        # 1 de 1 es el máximo posible: no hace falta ningún umbral para
        # decidir que esa provincia se sombree al tope.
        vista = build_map(_nombres_sembrados(), {"La Habana": 1})
        por_nombre = {p.name: p for p in vista.provinces}

        assert por_nombre["La Habana"].density_ratio == 1.0


class TestEstadoDePublicacion:
    """§9.0: la interfaz distingue publicada de «en ingesta sin publicación».

    Son dos ejes distintos —una decisión y una medición— y el estado sale de
    cruzarlos. Confundirlos fue el bug que este slice corrige: el mapa mostraba
    cobertura y la llamaba publicación.
    """

    def test_con_dato_y_sin_publicar_es_ingesta_no_publicada(self) -> None:
        """El estado real de hoy: se recolecta y no se expone la serie."""
        vista = build_map(_nombres_sembrados(), {"La Habana": 113})
        habana = next(p for p in vista.provinces if p.name == "La Habana")

        assert habana.has_data
        assert not habana.is_published
        assert habana.state == "en-ingesta"
        assert habana.label == "Recogiendo datos, todavía sin publicar"

    def test_publicada_se_muestra_como_tal(self) -> None:
        vista = build_map(_nombres_sembrados(), {"La Habana": 113}, {"La Habana"})
        habana = next(p for p in vista.provinces if p.name == "La Habana")

        assert habana.state == "publicada"
        assert vista.published == 1

    def test_la_publicacion_manda_sobre_la_presencia_de_dato(self) -> None:
        """Publicar es una decisión ya tomada; esconderla porque el conteo está
        en cero sería el opuesto de lo que §9.0 pide mostrar."""
        vista = build_map(_nombres_sembrados(), {}, {"La Habana"})
        habana = next(p for p in vista.provinces if p.name == "La Habana")

        assert habana.rows == 0
        assert habana.state == "publicada"

    def test_omitir_el_conjunto_equivale_a_ninguna_publicada(self) -> None:
        """El default no afirma nada: una provincia sale publicada solo si
        alguien lo decidió explícitamente en la base."""
        vista = build_map(_nombres_sembrados(), {"La Habana": 113})

        assert vista.published == 0
        assert all(not p.is_published for p in vista.provinces)

    def test_una_provincia_desconocida_no_rompe_el_mapa(self) -> None:
        """Un nombre que no viene en `province_names` se ignora en vez de
        tumbar la página pública."""
        vista = build_map(
            _nombres_sembrados(), {"La Habana": 5, "Provincia Inventada": 99}
        )

        assert len(vista.provinces) == 16
        assert vista.total_rows == 5

    def test_las_provincias_salen_ordenadas(self) -> None:
        nombres = [p.name for p in build_map(_nombres_sembrados(), {}).provinces]

        assert nombres == sorted(nombres)


class TestRutaPublica:
    def test_la_raiz_responde_sin_credenciales(self, client: TestClient) -> None:
        """Es la capa pública: un 401 acá sería el bug, no la protección."""
        response = client.get("/")

        assert response.status_code == 200

    def test_manda_los_datos_de_las_dieciseis_provincias(
        self, client: TestClient, session: Session
    ) -> None:
        """`#ph-map-data` (`map_provinces_json`) es el contrato que
        `estiloDeProvincia()` consume: una fila por provincia, con el mismo
        slug que trae `cuba_provincias.geojson`, así que el JS las puede
        emparejar."""
        _seed_provincias(session)

        texto = client.get("/").text
        filas = _map_data(texto)
        slugs_geojson = {f["properties"]["slug"] for f in _geojson()["features"]}

        assert len(filas) == 16
        assert {fila["slug"] for fila in filas} == slugs_geojson
        for fila in filas:
            assert fila["state"] == "sin-datos"  # base vacía, ninguna con dato

    def test_con_la_base_vacia_no_promete_cobertura(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_provincias(session)

        texto = client.get("/").text

        assert "0 de 16 provincias" in texto
        assert all(fila["state"] == "sin-datos" for fila in _map_data(texto))

    def test_declara_que_ninguna_provincia_esta_publicada(
        self, client: TestClient
    ) -> None:
        """§9.0: «La ausencia de datos se comunica como decisión metodológica,
        no como falla». La página lo dice, no lo deja implícito."""
        texto = client.get("/").text

        assert "Recogiendo datos, todavía sin publicar" in texto
        assert "Todavía <strong>ninguna</strong> cumple" in texto

    def test_el_progreso_va_como_conteo_no_como_porcentaje(
        self, client: TestClient
    ) -> None:
        """§9.0 pide «su progreso hacia el umbral», pero el denominador N está
        sin calibrar y §12.6 prohíbe inventarlo. El conteo crudo es honesto; un
        porcentaje fabricaría el umbral."""
        texto = client.get("/").text

        assert "% del umbral" not in texto

    def test_no_expone_la_herramienta_del_operador(self, client: TestClient) -> None:
        """`_public_shell.html` no extiende el shell del operador justamente
        para esto. Si alguien lo 'unifica', esto se pone rojo."""
        texto = client.get("/").text

        assert 'href="/curation"' not in texto
        assert 'href="/control"' not in texto
        assert 'href="/catalog"' not in texto

    def test_el_tamano_de_muestra_es_visible(self, client: TestClient) -> None:
        """§9.0 lo exige desde el día 1, en lugar de un titular de precio."""
        texto = client.get("/").text

        assert "Observaciones por provincia" in texto

    def test_el_mapa_no_colorea_por_precio(self, client: TestClient) -> None:
        """El coloreado verde/amarillo/rojo de §6.7 necesita un umbral de
        muestra que §12.6 prohíbe inventar. El mapa distingue cobertura y nada
        más; las tarjetas sí publican precio, con su rótulo y su n.

        No hay ningún `is-caro`/`is-barato`/`is-promedio` en ningún lado del
        proyecto —el coloreado real corre en JS y solo conoce `state` y
        `density_ratio`, ninguno de los dos deriva de un precio—, así que
        basta con que la página entera nunca los mencione."""
        texto = client.get("/").text

        for clase in ("is-caro", "is-barato", "is-promedio"):
            assert clase not in texto


class TestCapaDelMapa:
    """Selector de capa (2026-09-11): «publicación» (default, sin tocar) y
    «densidad», presentacional, sin umbral que calibrar.

    Con el mapa Leaflet (2026-09-12) el color real de cada polígono lo pone
    `estiloDeProvincia()` en JS, que pytest no ejecuta: lo que estos casos
    afirman es el contrato de datos que ese script lee —`data-capa` en
    `.ph-stage` y el `capa` que viaja en el `query` de cada fila de
    `#ph-map-data`—, no el color final en pantalla."""

    def test_por_defecto_es_la_capa_de_publicacion(self, client: TestClient) -> None:
        texto = client.get("/").text

        assert 'class="ph-layer-toggle"' in texto
        assert 'data-capa="publicacion"' in texto

    def test_con_capa_densidad_queda_en_data_capa(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_provincias(session)

        texto = client.get("/?capa=densidad").text

        assert 'data-capa="densidad"' in texto
        for fila in _map_data(texto):
            assert 0.0 <= fila["density_ratio"] <= 1.0

    def test_una_capa_invalida_no_rompe_cae_a_publicacion(
        self, client: TestClient
    ) -> None:
        r = client.get("/?capa=lo-que-sea")

        assert r.status_code == 200
        assert 'data-capa="publicacion"' in r.text

    def test_el_enlace_de_una_provincia_preserva_la_capa_elegida(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_provincias(session)

        texto = client.get("/?capa=densidad").text

        assert "capa=densidad" in texto.split('id="precios"')[0]
        # El `query` de cada fila del mapa —a donde navega el clic sobre el
        # polígono— también la lleva.
        assert all("capa=densidad" in fila["query"] for fila in _map_data(texto))

    def test_la_provincia_elegida_queda_en_data_provincia(
        self, client: TestClient, session: Session
    ) -> None:
        # Lo lee `inicializarMapa()` del lado del cliente; vacío sin
        # ninguna elegida.
        session.add(Province(code="hab", name="La Habana"))
        session.commit()

        assert 'data-provincia=""' in client.get("/").text
        assert 'data-provincia="la-habana"' in client.get("/?provincia=la-habana").text

    def test_elegir_provincia_no_fuerza_data_capa_a_densidad(
        self, client: TestClient, session: Session
    ) -> None:
        """Acercarse a una provincia (2026-09-11) cambia el enfoque visual a
        densidad de anuncios, pero eso lo decide el JS leyendo
        `data-provincia` —no un cambio en `data-capa`, que sigue reflejando
        el selector manual del menú tal cual el operador lo dejó (default
        «publicación»)."""
        session.add(Province(code="hab", name="La Habana"))
        session.commit()

        texto = client.get("/?provincia=la-habana").text

        assert 'data-capa="publicacion"' in texto
        assert 'data-provincia="la-habana"' in texto

    def test_con_provincia_elegida_el_mapa_sigue_mandando_las_dieciseis(
        self, client: TestClient, session: Session
    ) -> None:
        """Con el SVG (hasta el 2026-09-11), elegir una provincia hacía que
        el server dejara de dibujar las otras 15. Con Leaflet (2026-09-12)
        ya no tiene sentido: la geometría es un archivo estático que no
        cambia con el filtro, y cuál provincia se resalta es una decisión
        visual de `estiloDeProvincia()` en el cliente, no una omisión de
        datos — las 16 siguen viajando siempre en `#ph-map-data`."""
        _seed_provincias(session)

        filas = _map_data(client.get("/?provincia=la-habana").text)

        assert len(filas) == 16
        assert "la-habana" in {fila["slug"] for fila in filas}

    def test_la_leyenda_de_densidad_cuenta_solo_esa_provincia(
        self, client: TestClient, session: Session
    ) -> None:
        """La leyenda de abajo del mapa es Jinja puro (no depende del mapa
        Leaflet ni de su JS): con provincia elegida cuenta esa sola, no las
        16 —desincronizada antes de la entrada del 2026-09-11 que lo
        corrigió—."""
        session.add(Province(code="hab", name="La Habana"))
        session.commit()

        texto = client.get("/?provincia=la-habana").text

        assert "en La Habana</p>" in texto
        assert "en total</p>" not in texto


class TestEnlaceDeEvidenciaEnLaSerie:
    """Transparency Layer (docs/decisions.md, 2026-08-25): cada punto del
    gráfico de evolución es un anuncio real, y solo el de evidencia de texto
    puede enlazar a `/anuncio/{id}` — el de imagen no publica (§10.6).

    Las fechas van RELATIVAS a hoy. La versión original fijaba 2026-08-10 y
    2026-08-12, que el 2026-08-25 caían cómodas dentro de la ventana por
    defecto de 30 días (`product_nav.DEFAULT_WINDOW_DAYS`) y el 2026-09-10
    dejaron de caer: `build_series` recorta con `hoy - timedelta(days=30)`, así
    que el gráfico se quedó con un punto y el enlace desapareció. El test de
    texto se puso rojo solo, por el calendario, y el de imagen pasó a estar
    verde por la razón equivocada — no había enlace que ocultar.

    `tests/test_public_filter.py` ya traía la lección en su helper `_hace()`:
    todo test que pegue a la vista pública fecha relativo a hoy, nunca duro.
    `tests/test_series_view.py` resuelve lo mismo por el otro lado, pasándole
    un `hoy=` explícito a `build_series`.
    """

    def _ingestion(
        self, session: Session, *, hash_: str, content_type: str
    ) -> RawIngestion:
        row = RawIngestion(
            source_type=SourceType.INBOX_TEXT
            if content_type == ContentType.TEXT
            else SourceType.INBOX_IMAGE,
            file_path=f"raw/{hash_}.dat",
            content_hash=hash_,
            content_type=content_type,
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        session.add(row)
        session.flush()
        return row

    def _precio(
        self,
        session: Session,
        *,
        product: Product,
        province: Province,
        source: RawIngestion,
        dia: date,
    ) -> None:
        session.add(
            PriceHistory(
                product_id=product.id,
                province_id=province.id,
                price=Decimal("300"),
                currency=Currency.CUP.value,
                source_id=source.id,
                unit="lb",
                sale_format=SaleFormat.RETAIL.value,
                quantity=Decimal("1"),
                published_date=dia,
            )
        )

    def test_evidencia_de_texto_enlaza_a_anuncio(
        self, client: TestClient, session: Session
    ) -> None:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        habana = Province(code="hab", name="La Habana")
        session.add_all([arroz, habana])
        session.commit()
        origen = self._ingestion(session, hash_="hash-texto", content_type=ContentType.TEXT)
        hoy = date.today()
        self._precio(session, product=arroz, province=habana, source=origen, dia=hoy - timedelta(days=3))
        self._precio(session, product=arroz, province=habana, source=origen, dia=hoy - timedelta(days=1))
        session.commit()

        texto = client.get("/").text

        assert f'href="/anuncio/{origen.id}"' in texto

    def test_evidencia_de_imagen_no_enlaza(
        self, client: TestClient, session: Session
    ) -> None:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        habana = Province(code="hab", name="La Habana")
        session.add_all([arroz, habana])
        session.commit()
        origen = self._ingestion(session, hash_="hash-imagen", content_type=ContentType.IMAGE)
        hoy = date.today()
        self._precio(session, product=arroz, province=habana, source=origen, dia=hoy - timedelta(days=3))
        self._precio(session, product=arroz, province=habana, source=origen, dia=hoy - timedelta(days=1))
        session.commit()

        texto = client.get("/").text

        assert f'href="/anuncio/{origen.id}"' not in texto
        # La tabla de anuncios de «Cómo se movió» la nombra, sin enlace.
        assert ">captura</span>" in texto


class TestElMapaEsLaApp:
    """El mapa cubre el lienzo entero y el cromo flota encima (2026-09-12,
    ampliado 2026-09-13 con el rediseño «Brújula»).

    Pedido original del operador: «El mapa debe ser toda la app […] los menús
    de los productos y las provincias y todo lo demás debe salir sobre el
    mapa de forma flotante». El primer intento puso los chips y la provincia
    en una fila a lo ancho con `space-between`; no convenció («necesito algo
    mas creativo… sentir que el topbar menu y mapa es una sola cosa») y se
    evaluaron variantes en un canvas de Claude Design. La elegida —«Brújula»—
    hace del avatar el control mismo, anclado a la esquina como el
    layers/zoom de un mapa, con un dock vertical y una píldora de vidrio para
    chips+provincia en vez de una barra horizontal.

    **Qué puede y qué no puede afirmar un test de pytest acá.** Que el compás,
    el título y el dock se lean como una sola pieza lo decide el CSS (el
    hilo, el vidrio, el desenfoque), y sin navegador no hay layout que medir.
    Lo que sí se verifica es la precondición estructural —chips y provincia
    HERMANOS dentro de la misma píldora de filtros, no dos bloques
    apilados— más los z-index del cromo nuevo. La verificación visual es del
    operador.
    """

    def test_el_riel_de_chips_y_la_provincia_comparten_la_pildora_de_filtros(
        self, client: TestClient, session: Session
    ) -> None:
        # Los dos grupos son condicionales y el test pasaría en falso si no
        # encontrara nada que ordenar: el riel necesita al menos un producto
        # piloto (`product_chips`), y el grupo de provincia, más de un ítem
        # en `province_nav` —que se arma con las provincias que TIENEN dato
        # piloto, más la elegida (`routers/public.py`)—. Con la provincia en
        # la URL alcanza sembrarla, y además es el estado que el operador
        # pidió ver: el `<select>` y el chip juntos.
        session.add(
            Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        )
        _seed_provincias(session)
        texto = client.get("/?provincia=la-habana").text

        pildora = texto.index('class="ph-compass-filters"')
        chips = texto.index('class="ph-chip-rail"')
        provincia = texto.index('class="ph-province-group"')
        # El `<select>` y el chip de «quitar la provincia» viven los dos
        # adentro del grupo, no uno en cada punta de la píldora.
        assert texto.index('class="ph-province-form"') > provincia
        assert texto.index('<span class="ph-province-chip">') > provincia
        # El escenario del mapa es el primer hermano DESPUÉS de la píldora:
        # que los dos grupos caigan antes que él es lo que prueba que están
        # los dos adentro del mismo contenedor y no uno afuera.
        escenario = texto.index('class="ph-stage"')

        assert pildora < chips < provincia < escenario

    def test_la_pildora_de_filtros_va_sin_vidrio_ajuste_del_operador(
        self, client: TestClient
    ) -> None:
        """Ya no es una fila a lo ancho con `space-between`: es una isla más,
        anclada arriba a la derecha. El operador la probó a mano en DevTools
        y pidió dejarla SIN fondo, borde, sombra ni `backdrop-filter` — los
        chips y el `<select>` ya traen su propia superficie, y la caja extra
        se sentía como vidrio sobre vidrio."""
        texto = client.get("/").text
        regla = texto[texto.index("  .ph-compass-filters {") :]
        regla = regla[: regla.index("}")]

        assert "position: absolute" in regla
        assert "right:" in regla
        assert "background" not in regla
        assert "box-shadow" not in regla
        assert "backdrop-filter" not in regla

    def test_el_riel_de_chips_tiene_tope_de_ancho_y_alto(
        self, client: TestClient
    ) -> None:
        """Hoy son 5 productos y caben en una fila; el reporte de mejoras no
        consideradas (2026-09-14) marcó que sin un tope, un catálogo grande
        (200 productos) crece la isla de filtros hasta tapar el mapa: a lo
        ancho porque `.ph-compass-filters` no tenía `max-width` y el
        `flex-wrap` se estira hasta donde haya sitio, y a lo alto porque
        `.ph-chip-rail` no tenía techo. Con 5 chips ninguno de los dos topes
        se nota (nunca alcanzan a scrollear)."""
        texto = client.get("/").text

        isla = texto[texto.index("  .ph-compass-filters {") :]
        isla = isla[: isla.index("}")]
        assert "max-width:" in isla

        riel = texto[texto.index("  .ph-chip-rail {") :]
        riel = riel[: riel.index("}")]
        assert "max-height:" in riel
        assert "overflow-y: auto" in riel

    def test_el_escenario_del_mapa_va_a_sangre_y_el_cromo_por_encima(
        self, client: TestClient
    ) -> None:
        """`.ph-stage` sale del flujo y cubre el lienzo; el cromo tiene que
        quedar por encima de las capas internas de Leaflet (que llegan a
        1000), no en el 1..4 de antes de que existiera Leaflet."""
        texto = client.get("/").text
        escenario = texto[texto.index("  .ph-stage {") :]
        escenario = escenario[: escenario.index("}")]

        assert "position: absolute" in escenario
        assert "inset: 0" in escenario
        assert "z-index: 0" in escenario

        for clase in (".ph-compass", ".ph-menu", ".ph-compass-filters", ".ph-dock-layer", ".ph-loading"):
            # `.ph-compass`/`.ph-menu`/`.ph-compass-filters` tienen más de una
            # regla con el mismo selector+llave (la de posición/z-index, y la
            # que oculta por defecto o la del media query angosto, sin
            # z-index) — se busca la que trae `z-index:`, no la primera
            # aparición cruda del selector.
            desde = texto.index(f"  {clase} {{")
            regla = texto[desde:]
            while "z-index:" not in regla[: regla.index("}") + 1]:
                desde = texto.index(f"  {clase} {{", desde + 1)
                regla = texto[desde:]
            regla = regla[: regla.index("}")]
            z = regla[regla.index("z-index:") :]
            assert int(z[len("z-index:") : z.index(";")]) > 1000, clase

    def test_el_compas_el_titulo_y_el_dock_van_unidos_por_un_hilo(
        self, client: TestClient
    ) -> None:
        """El pedido era «sentir que topbar, menú y mapa son una sola cosa»:
        `.ph-compass-thread`/`.ph-compass-link` son el hilo decorativo que
        conecta las piezas — sin ellos, el compás, el título y el dock
        volverían a leerse como tres cosas sueltas flotando."""
        texto = client.get("/").text

        assert 'class="ph-compass-thread" aria-hidden="true"' in texto
        assert 'class="ph-compass-link" aria-hidden="true"' in texto
        # El hilo va ANTES en el DOM que el compás: pinta detrás sin z-index
        # propio (mismo nivel 1300; lo último en el DOM pinta encima).
        hilo = texto.index('class="ph-compass-thread"')
        compas = texto.index('class="ph-compass"')
        assert hilo < compas

    def test_el_zoom_del_mapa_vive_en_el_dock_y_leaflet_no_dibuja_el_suyo(
        self, client: TestClient
    ) -> None:
        """Leaflet dibuja su propio control de zoom arriba a la izquierda por
        defecto — justo donde vive el compás. `zoomControl: false` lo saca;
        `[data-zoom-in]`/`[data-zoom-out]` lo reemplazan, llamando a
        `mapaLeaflet.zoomIn()/zoomOut()` por delegación de evento."""
        texto = client.get("/").text

        assert "zoomControl: false" in texto
        assert "data-zoom-in" in texto
        assert "data-zoom-out" in texto
        assert "mapaLeaflet.zoomIn()" in texto
        assert "mapaLeaflet.zoomOut()" in texto

    def test_el_muelle_de_ventanas_no_vive_adentro_del_mapa(
        self, client: TestClient, session: Session
    ) -> None:
        """El muelle era hijo de `.ph-stage`. Con el escenario cubriendo el
        lienzo entero, ahí habría tapado la barra superior: una ventana
        arrastrada hacia arriba se metía abajo de la barra y no había forma de
        agarrar su título. Ahora es hermano y ocupa el hueco que queda."""
        _seed_provincias(session)
        texto = client.get("/").text

        mapa = texto.index('id="ph-leaflet-map"')
        capa_del_muelle = texto.index('class="ph-dock-layer"')
        muelle = texto.index('class="ph-dock"')

        assert mapa < capa_del_muelle < muelle

    def test_el_credito_del_mapa_no_se_duplica(self, client: TestClient) -> None:
        """Había un `<p class="ph-attrib">` en la esquina inferior derecha y el
        control de atribución de Leaflet en esa misma esquina: dos cajas
        pisándose. Quedó el de Leaflet, con los dos créditos adentro."""
        texto = client.get("/").text

        assert "ph-attrib" not in texto
        # Leaflet recibe los dos créditos juntos y los pone una sola vez.
        assert texto.count("OpenStreetMap</a> contributors") == 1
        assert 'Provincias: <a href="https://www.naturalearthdata.com/">' in texto

        # El pie del shell (`_public_shell.html`) tiene su propia línea de
        # Natural Earth desde antes de Leaflet, y se queda: habla de la
        # procedencia del dato (§6.5), no es el crédito de la esquina del
        # mapa. Lo que se eliminó fue el `<p class="ph-attrib">`, que caía
        # justo encima del control de atribución de Leaflet.
        assert "Geometría de las provincias derivada de" in texto


class TestDockOcultoHastaTocarElAvatar:
    """Ajuste del operador sobre «Brújula» (2026-09-13): el dock vertical
    (`.ph-menu`) ya no está siempre visible — aparece al pasar el mouse, al
    llegar por teclado o al tocar/cliquear el avatar, y el compás pasa a ser
    un `<button>` de verdad.

    El operador también reescribió `.ph-compass-filters` a mano en DevTools
    (sin fondo/borde/sombra/`display: flex`) y pidió dejarla exactamente
    así; ese caso lo cubre `TestElMapaEsLaApp`.
    """

    def test_el_compas_es_un_boton_y_el_dock_vive_adentro_del_envoltorio(
        self, client: TestClient
    ) -> None:
        texto = client.get("/").text

        envoltorio = texto.index('class="ph-compass-nav"')
        compas = texto.index('<button type="button" class="ph-compass"')
        hilo = texto.index('class="ph-compass-thread"')
        dock = texto.index('<nav class="ph-menu"')

        # Los tres viven ADENTRO de `.ph-compass-nav`, en ese orden: el
        # hilo primero (pinta detrás), después el botón, después el dock.
        assert envoltorio < hilo < compas < dock

    def test_el_dock_arranca_oculto_y_focus_within_o_is_active_lo_revelan_por_css(
        self, client: TestClient
    ) -> None:
        """No hay `display: none` de por medio —eso sacaría los ítems del
        orden de tabulación para siempre—: es `opacity`/`visibility`.

        `:hover` de CSS quedó afuera a propósito (ajuste del mismo día): sin
        un rectángulo que cubra el hueco entre avatar y dock, un `:hover`
        puro cerraría el dock a mitad de camino al cruzar ese hueco por el
        hilo de 2px. El hover real lo maneja JS con demora
        (`abrirCompas`/`programarCierreCompas`, ver el otro test); acá solo
        quedan los dos disparadores que sí son puro CSS: `:focus-within`
        (teclado) y `.is-active` (la clase que ese JS y el toque alternan)."""
        texto = client.get("/").text

        # El dock necesita `visibility` (sus enlaces son focales); el hilo,
        # el enlace decorativo y el título NO —van con `opacity` nada más,
        # ver el siguiente test para el porqué.
        regla_oculta = texto[texto.index("  .ph-menu {") :]
        regla_oculta = regla_oculta[: regla_oculta.index("}")]
        assert "opacity: 0" in regla_oculta
        assert "visibility: hidden" in regla_oculta
        assert "display: none" not in regla_oculta

        regla_revelada = texto[texto.index(".ph-compass-nav:focus-within .ph-compass-thread") :]
        regla_revelada = regla_revelada[: regla_revelada.index("}")]
        assert ":focus-within" in regla_revelada
        assert ".is-active" in regla_revelada
        assert ".ph-compass-title-group" in regla_revelada
        assert "opacity: 1" in regla_revelada

        regla_dock_visible = texto[
            texto.index(".ph-compass-nav:focus-within .ph-menu, .ph-compass-nav.is-active .ph-menu") :
        ]
        regla_dock_visible = regla_dock_visible[: regla_dock_visible.index("}")]
        assert "visibility: visible" in regla_dock_visible

        # El rectángulo invisible de la primera versión (position/width/
        # height/pointer-events en `.ph-compass-nav`) tapaba el arrastre del
        # mapa en celular — no puede volver.
        assert ".ph-compass-nav {" not in texto

    def test_el_titulo_se_oculta_sin_romper_el_nombre_accesible_de_la_seccion(
        self, client: TestClient
    ) -> None:
        """El operador pidió que «el header que dice guani saimc y todo lo
        demas» se oculte igual que el dock (2026-09-13). Pero
        `<section class="ph-canvas">` usa `aria-labelledby="ph-canvas-title"`
        —apunta al `<h1>` de ese título— y `visibility: hidden` (o
        `display: none`) saca a un elemento del cómputo de nombre accesible:
        ocultar el título así habría dejado a la sección sin nombre la
        mayor parte del tiempo. Por eso el título se oculta con `opacity`
        nada más, nunca `visibility`."""
        texto = client.get("/").text

        assert 'aria-labelledby="ph-canvas-title"' in texto
        assert 'id="ph-canvas-title"' in texto

        regla_oculta = texto[
            texto.index(".ph-compass-thread, .ph-compass-link, .ph-compass-title-group {") :
        ]
        regla_oculta = regla_oculta[: regla_oculta.index("}")]
        assert "opacity: 0" in regla_oculta
        assert "visibility" not in regla_oculta

        # El botón trae su propio nombre por si un lector de pantalla
        # aterriza ahí directo, sin pasar por el título.
        assert 'aria-label="Guani · SAIMC · abrir el menú"' in texto

    def test_el_hover_de_mouse_lo_maneja_js_con_demora_no_hover_de_css(
        self, client: TestClient
    ) -> None:
        """`mouseover`/`mouseout` burbujean —`mouseenter`/`mouseleave` no—,
        así que sirven para delegar en `document`; `relatedTarget` distingue
        salir de veras del envoltorio de pasar a otro hijo suyo. La demora
        (300ms) tolera cruzar el hueco entre avatar y dock sin que el dock se
        cierre a mitad de camino."""
        texto = client.get("/").text

        assert "function abrirCompas()" in texto
        assert "function programarCierreCompas()" in texto
        assert 'document.addEventListener("mouseover"' in texto
        assert 'document.addEventListener("mouseout"' in texto
        assert "ev.relatedTarget" in texto
        assert "}, 300)" in texto

    def test_tocar_el_avatar_alterna_is_active_por_js(self, client: TestClient) -> None:
        """En táctil no hay `:hover`: `alternarCompas` es el mecanismo real
        para abrir/cerrar el dock con el dedo, delegado en `document` por la
        misma razón que `zoomDelMapa` y `cerrarOptimista` — el compás entero
        vive adentro de `#ph-live` y se reemplaza en cada swap de htmx."""
        texto = client.get("/").text

        assert "function alternarCompas(ev)" in texto
        assert 'envoltorio.classList.toggle("is-active")' in texto
        assert 'document.addEventListener("click", alternarCompas)' in texto


class TestFiltrosEnAngostoVanAlLadoContrarioDelCompas:
    """Ajuste del operador (2026-09-13): «en el movil… los productos y las
    provincias ahora se van a convertir en una barra situada en el lado
    contrario a la barra que sale de el avatar de guani». El compás vive
    arriba a la izquierda —título y dock se despliegan desde ahí—; en
    angosto la píldora de chips+provincia se muda al lado opuesto, abajo
    del todo, como una barra propia a lo ancho. Solo en angosto: la regla
    de escritorio (sin `display: flex`, tal como la dejó el operador en
    DevTools) no se toca."""

    def _regla_angosta(self, texto: str, selector: str) -> str:
        """La primera ocurrencia de `selector {` que trae `left: 0` es la
        del `@media` angosto — la de escritorio no toca `left`."""
        desde = 0
        while True:
            desde = texto.index(f"{selector} {{", desde)
            fin = texto.index("}", desde)
            cuerpo = texto[desde:fin]
            if "left: 0" in cuerpo:
                return cuerpo
            desde = fin

    def test_la_pildora_de_filtros_baja_al_fondo_en_angosto(
        self, client: TestClient
    ) -> None:
        texto = client.get("/").text
        regla = self._regla_angosta(texto, "    .ph-compass-filters")

        assert "top: auto" in regla
        assert "bottom: 0" in regla
        assert "display: flex" in regla

    def test_la_bandeja_de_ventanas_le_deja_hueco_a_la_barra_de_abajo(
        self, client: TestClient
    ) -> None:
        """Las dos cosas se anclan abajo en angosto — sin coordinarlas, la
        bandeja de ventanas (`.ph-dock`) quedaría tapada por la barra nueva
        de chips+provincia, o al revés. `inset` es un atajo (no hay
        `left: 0` literal que buscar, a diferencia de `.ph-compass-filters`),
        así que alcanza con el valor completo, único en el archivo."""
        texto = client.get("/").text

        assert "inset: auto 0 4.5rem 0" in texto

    def test_la_leyenda_no_queda_siempre_abierta_en_angosto(
        self, client: TestClient
    ) -> None:
        """La leyenda (Publicación/Densidad) seguía «muy cerca de los
        productos» y molestando aun reservándole hueco (ajuste del operador,
        el mismo día: «hay que ser creativo… o lo movemos de lugar o lo
        ocultamos y mostramos de alguna forma»). Ver
        `TestLaLeyendaColapsaEnAngosto` para el mecanismo completo."""
        texto = client.get("/").text
        regla = texto[texto.index(".ph-canvas-legend.is-active") :]
        regla = regla[: regla.index("}")]

        assert "opacity: 1" in regla


class TestLaLeyendaColapsaEnAngosto:
    """La leyenda (Publicación/Densidad) colapsa en angosto (2026-09-13, en
    dos pasos el mismo día): primero a un botón redondo suelto en su propia
    esquina; el operador pidió mudar ESE botón adentro del dock del compás
    —«quiero que ese boton este dentro de la barra… que aparece cuando se
    interactua con [el avatar]»—, no dejarlo flotando aparte. El botón y la
    leyenda ya no comparten un envoltorio común en el DOM: viven en dos
    puntos distintos de la página, unidos solo por JS. En escritorio no
    cambia nada: la leyenda sigue siempre abierta, como toda la vida."""

    def test_el_boton_esta_oculto_en_escritorio(self, client: TestClient) -> None:
        texto = client.get("/").text
        regla = texto[texto.index("  .ph-legend-toggle { display: none; }") :]
        regla = regla[: regla.index(";")]

        assert "display: none" in regla

    def test_el_boton_vive_adentro_del_dock_del_compas(
        self, client: TestClient
    ) -> None:
        texto = client.get("/").text

        dock = texto.index('<nav class="ph-menu"')
        cierre_dock = texto.index("</nav>", dock)
        boton = texto.index('class="ph-menu-item ph-legend-toggle"')

        assert dock < boton < cierre_dock

    def test_el_boton_aparece_en_angosto_y_la_leyenda_arranca_oculta(
        self, client: TestClient
    ) -> None:
        texto = client.get("/").text

        regla_boton = texto[texto.index(".ph-menu-item.ph-legend-toggle {") :]
        regla_boton = regla_boton[: regla_boton.index("}")]
        assert "display: flex" in regla_boton

        # La MISMA búsqueda de `.ph-canvas-legend {` que ya usan otros tests
        # de esta clase encuentra la regla de escritorio primero (sin
        # `opacity`); la de angosto es la siguiente aparición.
        desde = texto.index("  .ph-canvas-legend {")
        desde = texto.index("  .ph-canvas-legend {", desde + 1)
        regla_oculta = texto[desde : texto.index("}", desde)]
        assert "opacity: 0" in regla_oculta
        assert "visibility: hidden" in regla_oculta

    def test_tocar_el_boton_revela_la_leyenda_por_js(self, client: TestClient) -> None:
        """Mismo patrón que `alternarCompas`, sin la parte de hover: el botón
        solo existe en angosto (`display: none` en escritorio), así que no
        hace falta distinguir mouse de táctil acá. El botón y la leyenda
        viven en dos puntos distintos del DOM, así que esto apunta a
        `.ph-canvas-legend` directo, no a un envoltorio compartido."""
        texto = client.get("/").text

        assert "function alternarLeyenda(ev)" in texto
        assert 'leyenda.classList.toggle("is-active")' in texto
        assert 'boton.classList.toggle("is-open")' in texto
        assert 'document.addEventListener("click", alternarLeyenda)' in texto

    def test_el_boton_tiene_su_propio_icono_y_nombre_accesible(
        self, client: TestClient
    ) -> None:
        texto = client.get("/").text

        assert 'class="ph-menu-item ph-legend-toggle" aria-pressed="false" aria-label="Mostrar la leyenda del mapa"' in texto
        assert '<span class="m3-icon">layers</span>' in texto
