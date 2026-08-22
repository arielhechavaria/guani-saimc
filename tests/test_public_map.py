"""Mapa público: geometría, cobertura y la primera ruta sin operador.

Lo que estos casos protegen no es cómo se ve el mapa —eso se mira— sino tres
cosas que se rompen en silencio: que los nombres de la geometría sigan
coincidiendo con los de la tabla `provinces`, que una provincia sin dato
aparezca igual en vez de desaparecer, y que la vista pública no filtre la
herramienta del operador.
"""

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

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
        assert all(p.state == "sin-dato" for p in vista.provinces)

    def test_cruza_los_conteos_con_la_geometria(self) -> None:
        vista = build_map({"La Habana": 113, "Santiago de Cuba": 105})
        por_nombre = {p.name: p for p in vista.provinces}

        assert vista.covered == 2
        assert vista.total_rows == 218
        assert por_nombre["La Habana"].rows == 113
        assert por_nombre["La Habana"].state == "con-dato"
        assert por_nombre["Camagüey"].rows == 0
        assert por_nombre["Camagüey"].state == "sin-dato"

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

        assert "0 de 16 provincias" in texto
        assert "is-con-dato" not in texto.split("<svg")[1].split("</svg>")[0]

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

    def test_no_publica_ningun_precio_todavia(self, client: TestClient) -> None:
        """El coloreado por precio necesita un umbral de muestra que §12.6
        prohíbe inventar. Mientras no exista, la página muestra cobertura y
        nada más — publicar una cifra acá sería la regresión."""
        texto = client.get("/").text

        assert "CUP" not in texto
