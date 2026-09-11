"""Mapa público: geometría, cobertura y la primera ruta sin operador.

Lo que estos casos protegen no es cómo se ve el mapa —eso se mira— sino tres
cosas que se rompen en silencio: que los nombres de la geometría sigan
coincidiendo con los de la tabla `provinces`, que una provincia sin dato
aparezca igual en vez de desaparecer, y que la vista pública no filtre la
herramienta del operador.
"""

import importlib.util
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
from saimc.web.map_cuba import PROVINCE_PATHS, VIEWBOX
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


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class TestGeometria:
    def test_estan_las_dieciseis_provincias(self) -> None:
        assert len(PROVINCE_PATHS) == 16

    def test_los_nombres_son_los_de_la_tabla_provinces(self) -> None:
        """El cruce entre geometría y dato se hace por nombre, sin tabla de
        traducción en el medio. Natural Earth trae «Ciudad de la Habana», el
        nombre anterior a la reorganización de 2011, y `tools/build_cuba_map.py`
        lo mapea; si esa corrección se pierde al regenerar, La Habana queda
        muda y nada más avisaría."""
        assert set(PROVINCE_PATHS) == _nombres_sembrados()

    def test_ninguna_ruta_esta_vacia(self) -> None:
        for name, path in PROVINCE_PATHS.items():
            assert path.startswith("M"), name
            assert path.endswith("Z"), name
            # Un polígono necesita al menos tres vértices para encerrar área.
            assert path.count(",") >= 3, name

    def test_el_viewbox_tiene_las_cuatro_medidas(self) -> None:
        partes = VIEWBOX.split()

        assert len(partes) == 4
        assert [float(p) for p in partes[:2]] == [0.0, 0.0]
        assert all(float(p) > 0 for p in partes[2:])


class TestBuildMap:
    def test_sin_datos_las_dieciseis_entran_en_cero(self) -> None:
        """El cero es el dato: dice dónde no se está mirando. Una provincia sin
        observaciones tiene que aparecer igual, no desaparecer del mapa."""
        vista = build_map({})

        assert len(vista.provinces) == 16
        assert vista.covered == 0
        assert vista.total_rows == 0
        assert all(not p.has_data for p in vista.provinces)
        assert all(p.state == "sin-datos" for p in vista.provinces)

    def test_cruza_los_conteos_con_la_geometria(self) -> None:
        vista = build_map({"La Habana": 113, "Santiago de Cuba": 105})
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
        vista = build_map({})

        assert all(p.density_ratio == 0.0 for p in vista.provinces)

    def test_la_que_mas_tiene_pinta_a_fondo(self) -> None:
        vista = build_map({"La Habana": 113, "Santiago de Cuba": 105})
        por_nombre = {p.name: p for p in vista.provinces}

        assert por_nombre["La Habana"].density_ratio == 1.0
        assert por_nombre["Santiago de Cuba"].density_ratio == pytest.approx(
            105 / 113
        )
        assert por_nombre["Camagüey"].density_ratio == 0.0

    def test_una_sola_observacion_en_una_provincia_esa_pinta_a_fondo(self) -> None:
        # 1 de 1 es el máximo posible: no hace falta ningún umbral para
        # decidir que esa provincia se sombree al tope.
        vista = build_map({"La Habana": 1})
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
        vista = build_map({"La Habana": 113})
        habana = next(p for p in vista.provinces if p.name == "La Habana")

        assert habana.has_data
        assert not habana.is_published
        assert habana.state == "en-ingesta"
        assert habana.label == "Recogiendo datos, todavía sin publicar"

    def test_publicada_se_muestra_como_tal(self) -> None:
        vista = build_map({"La Habana": 113}, {"La Habana"})
        habana = next(p for p in vista.provinces if p.name == "La Habana")

        assert habana.state == "publicada"
        assert vista.published == 1

    def test_la_publicacion_manda_sobre_la_presencia_de_dato(self) -> None:
        """Publicar es una decisión ya tomada; esconderla porque el conteo está
        en cero sería el opuesto de lo que §9.0 pide mostrar."""
        vista = build_map({}, {"La Habana"})
        habana = next(p for p in vista.provinces if p.name == "La Habana")

        assert habana.rows == 0
        assert habana.state == "publicada"

    def test_omitir_el_conjunto_equivale_a_ninguna_publicada(self) -> None:
        """El default no afirma nada: una provincia sale publicada solo si
        alguien lo decidió explícitamente en la base."""
        vista = build_map({"La Habana": 113})

        assert vista.published == 0
        assert all(not p.is_published for p in vista.provinces)

    def test_una_provincia_desconocida_no_rompe_el_mapa(self) -> None:
        """Un nombre que no existe en la geometría se ignora en vez de tumbar
        la página pública."""
        vista = build_map({"La Habana": 5, "Provincia Inventada": 99})

        assert len(vista.provinces) == 16
        assert vista.total_rows == 5

    def test_las_provincias_salen_ordenadas(self) -> None:
        nombres = [p.name for p in build_map({}).provinces]

        assert nombres == sorted(nombres)


class TestRutaPublica:
    def test_la_raiz_responde_sin_credenciales(self, client: TestClient) -> None:
        """Es la capa pública: un 401 acá sería el bug, no la protección."""
        response = client.get("/")

        assert response.status_code == 200

    def test_dibuja_las_dieciseis_provincias(self, client: TestClient) -> None:
        texto = client.get("/").text

        for name in PROVINCE_PATHS:
            assert name in texto
        # Con el prefijo `class="`, porque el nombre suelto también aparece en
        # las tres reglas del bloque `<style>`.
        assert texto.count('class="m3-map-province') == 16

    def test_con_la_base_vacia_no_promete_cobertura(self, client: TestClient) -> None:
        texto = client.get("/").text
        svg = texto.split("<svg")[1].split("</svg>")[0]

        assert "0 de 16 provincias" in texto
        assert "is-en-ingesta" not in svg
        assert svg.count("is-sin-datos") == 16

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

        (Reemplaza a un caso anterior que exigía que no apareciera «CUP» en
        toda la página. Era correcto mientras la página fuera solo el mapa, y
        dejó de serlo al leer §9.0 completa: no prohíbe el precio, prohíbe el
        valor puntual sin respaldo de muestra.)"""
        svg = client.get("/").text.split("<svg")[1].split("</svg>")[0]

        assert "is-con-dato" not in svg or "is-sin-dato" in svg
        for clase in ("is-caro", "is-barato", "is-promedio"):
            assert clase not in svg


class TestCapaDelMapa:
    """Selector de capa (2026-09-11): «publicación» (default, sin tocar) y
    «densidad», presentacional, sin umbral que calibrar."""

    def test_por_defecto_es_la_capa_de_publicacion(self, client: TestClient) -> None:
        texto = client.get("/").text
        svg = texto.split("<svg")[1].split("</svg>")[0]

        assert 'class="ph-layer-toggle"' in texto
        assert "is-densidad" not in svg
        assert "is-sin-datos" in svg  # las 16 sin dato, base vacía

    def test_con_capa_densidad_pinta_por_cantidad_no_por_estado(
        self, client: TestClient
    ) -> None:
        texto = client.get("/?capa=densidad").text
        svg = texto.split("<svg")[1].split("</svg>")[0]

        assert "is-densidad" in svg
        assert "--dens:" in svg
        for clase in ("is-publicada", "is-en-ingesta", "is-sin-datos"):
            assert clase not in svg

    def test_una_capa_invalida_no_rompe_cae_a_publicacion(
        self, client: TestClient
    ) -> None:
        r = client.get("/?capa=lo-que-sea")

        assert r.status_code == 200
        assert "is-densidad" not in r.text.split("<svg")[1].split("</svg>")[0]

    def test_el_enlace_de_una_provincia_preserva_la_capa_elegida(
        self, client: TestClient
    ) -> None:
        texto = client.get("/?capa=densidad").text

        assert "capa=densidad" in texto.split('id="precios"')[0]
        # El propio enlace del mapa a la hoja lleva la capa.
        svg = texto.split("<svg")[1].split("</svg>")[0]
        assert "capa=densidad" in svg

    def test_la_provincia_elegida_queda_en_data_provincia(
        self, client: TestClient, session: Session
    ) -> None:
        # Lo lee `actualizarZoomDelMapa()` del lado del cliente; vacío sin
        # ninguna elegida.
        session.add(Province(code="hab", name="La Habana"))
        session.commit()

        assert 'data-provincia=""' in client.get("/").text
        assert 'data-provincia="la-habana"' in client.get("/?provincia=la-habana").text

    def test_elegir_provincia_pinta_por_densidad_aunque_la_capa_sea_publicacion(
        self, client: TestClient, session: Session
    ) -> None:
        """Acercarse a una provincia (2026-09-11) cambia el enfoque visual a
        densidad de anuncios, sin que el operador haya tocado el selector de
        capa — que sigue en su default «publicación»."""
        session.add(Province(code="hab", name="La Habana"))
        session.commit()

        texto = client.get("/?provincia=la-habana").text
        svg = texto.split("<svg")[1].split("</svg>")[0]

        assert "is-densidad" in svg
        assert "--dens:" in svg
        for clase in ("is-publicada", "is-en-ingesta", "is-sin-datos"):
            assert clase not in svg

    def test_sin_provincia_elegida_la_capa_de_publicacion_sigue_de_estado(
        self, client: TestClient
    ) -> None:
        """El forzado a densidad es del zoom a una provincia, no un cambio al
        default global: sin provincia elegida el mapa entero sigue pintando
        por estado de publicación."""
        svg = client.get("/").text.split("<svg")[1].split("</svg>")[0]

        assert "is-densidad" not in svg


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
