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
    """El mapa cubre el lienzo entero y el cromo flota encima (2026-09-12).

    Pedido del operador: «El mapa debe ser toda la app […] los menús de los
    productos y las provincias y todo lo demás debe salir sobre el mapa de
    forma flotante. Los chips de productos y las provincias deben salir en la
    misma fila […] como cuando usamos flex y hacemos space between».

    **Qué puede y qué no puede afirmar un test de pytest acá.** Que los dos
    grupos queden efectivamente en una fila con el aire en el medio lo decide
    el CSS, y sin navegador no hay layout que medir. Lo que sí se verifica es
    la precondición estructural —que sean HERMANOS dentro de un solo
    contenedor de filtros, no dos bloques apilados— más la declaración del
    `space-between` en la hoja embebida. Si alguien vuelve a partir la fila en
    dos contenedores, esto se pone rojo; si alguien rompe el ancho de un chip,
    no. La verificación visual es del operador.
    """

    def test_el_riel_de_chips_y_la_provincia_comparten_la_fila_de_filtros(
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

        fila = texto.index('class="ph-filters"')
        chips = texto.index('class="ph-chip-rail"')
        provincia = texto.index('class="ph-province-group"')
        # El `<select>` y el chip de «quitar la provincia» viven los dos
        # adentro del grupo, no uno en cada punta de la fila.
        assert texto.index('class="ph-province-form"') > provincia
        assert texto.index('<span class="ph-province-chip">') > provincia
        # El escenario del mapa es el primer hermano DESPUÉS de la fila: que
        # los dos grupos caigan antes que él es lo que prueba que están los
        # dos adentro del mismo contenedor y no uno afuera.
        escenario = texto.index('class="ph-stage"')

        assert fila < chips < provincia < escenario

    def test_la_fila_de_filtros_reparte_con_space_between(
        self, client: TestClient
    ) -> None:
        texto = client.get("/").text
        regla = texto[texto.index("  .ph-filters {") :]
        regla = regla[: regla.index("}")]

        assert "justify-content: space-between" in regla
        assert "display: flex" in regla

    def test_el_escenario_del_mapa_va_a_sangre_y_el_cromo_por_encima(
        self, client: TestClient
    ) -> None:
        """`.ph-stage` sale del flujo y cubre el lienzo; el cromo tiene que
        quedar por encima de las capas internas de Leaflet (que llegan a
        1000), no en el 1..4 de antes."""
        texto = client.get("/").text
        escenario = texto[texto.index("  .ph-stage {") :]
        escenario = escenario[: escenario.index("}")]

        assert "position: absolute" in escenario
        assert "inset: 0" in escenario
        assert "z-index: 0" in escenario

        for clase in (".ph-topbar", ".ph-filters", ".ph-dock-layer", ".ph-loading"):
            regla = texto[texto.index(f"  {clase} {{") :]
            regla = regla[: regla.index("}")]
            z = regla[regla.index("z-index:") :]
            assert int(z[len("z-index:") : z.index(";")]) > 1000, clase

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
