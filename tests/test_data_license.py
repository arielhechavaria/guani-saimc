"""Esquema de uso de los datos y tope de la descarga (`docs/decisions.md`,
entrada «Uso de los datos: gratis con cita, comercial con acuerdo»).

Lo que estos casos protegen, escrito antes de tocar el código:

- `GET /datos.csv` entrega como mucho los últimos 30 días, contados igual que
  la ventana por defecto del tablero: entra todo anuncio con fecha
  `>= hoy - 30` (el día de hace 30 **entra**, el de hace 31 no). Un pedido más
  largo o más viejo se recorta, no da error; un rango enteramente anterior
  devuelve solo el encabezado.
- El tablero, el mapa y explorar siguen MOSTRANDO el período que se pida; solo
  se recorta el archivo, y junto al botón se avisa cuando eso pasa.
- El texto público ya no promete reutilización libre: uso personal,
  periodístico o académico con cita, y uso comercial con acuerdo previo con
  Leira Studio. Sin nombre de licencia ni jurisdicción.
"""

import csv
import io
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.config import Settings
from saimc.models import ContentType, PriceHistory, Product, Province, RawIngestion, SourceType
from saimc.models.raw_ingestion import ProcessingStatus
from saimc.web.app import app
from saimc.web.deps import get_db
from saimc.web.product_nav import DOWNLOAD_WINDOW_DAYS, clamp_download_window
from saimc.web.routers import public

RUTAS_DE_TEXTO = (
    "/quienes-somos",
    "/metodologia",
    "/preguntas",
    "/terminos",
    "/privacidad",
    "/uso-de-datos",
)

#: Días de antigüedad sembrados → precio, para reconocer cada fila en el CSV.
SEMBRADOS = {5: "105", 29: "129", 30: "130", 31: "131", 60: "160"}
#: Lo que cabe en la ventana de descarga: el borde de 30 días es inclusivo.
EN_VENTANA = {"105", "129", "130"}

ENCABEZADO = "producto,provincia,dia,precio_por_unidad,moneda,unidad,anuncio"


def _settings(**valores: str) -> Settings:
    return Settings(_env_file=None, **valores)  # type: ignore[arg-type, call-arg]


def _hace(dias: int) -> date:
    return date.today() - timedelta(days=dias)


def _visible(html: str) -> str:
    sin_bloques = re.sub(r"<(style|script)\b.*?</\1>", " ", html, flags=re.S)
    sin_comentarios = re.sub(r"<!--.*?-->", " ", sin_bloques, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", sin_comentarios)).strip()


def _pie(html: str) -> str:
    encontrado = re.search(r'<footer class="pf".*?</footer>', html, flags=re.S)
    assert encontrado is not None
    return encontrado.group(0)


def _cuerpo(html: str) -> str:
    return html.split("<footer")[0]


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
def con_contacto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(public, "get_settings", lambda: _settings(contact_email="hola@guani.example"))


@pytest.fixture
def sembrado(session: Session) -> None:
    """Un anuncio de arroz en La Habana por cada antigüedad de `SEMBRADOS`."""
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
    habana = Province(code="hab", name="La Habana")
    fuente = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path="raw/h-licencia.dat",
        content_hash="h-licencia",
        content_type=ContentType.TEXT,
        processing_status=ProcessingStatus.CURATED,
        captured_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    session.add_all([arroz, habana, fuente])
    session.flush()
    for dias, precio in SEMBRADOS.items():
        session.add(
            PriceHistory(
                provenance="human",
                product_id=arroz.id,
                province_id=habana.id,
                price=Decimal(precio),
                currency="CUP",
                source_id=fuente.id,
                unit="lb",
                sale_format="retail",
                quantity=Decimal("1"),
                published_date=_hace(dias),
            )
        )
    session.commit()


def _filas(client: TestClient, query: str = "") -> list[dict[str, str]]:
    respuesta = client.get(f"/datos.csv{query}")
    assert respuesta.status_code == 200
    return list(csv.DictReader(io.StringIO(respuesta.text)))


def _precios(client: TestClient, query: str = "") -> set[str]:
    return {str(int(Decimal(fila["precio_por_unidad"]))) for fila in _filas(client, query)}


class TestLaVentanaDeDescarga:
    """`clamp_download_window`, sin pasar por HTTP."""

    HOY = date(2026, 10, 5)
    PISO = date(2026, 9, 5)  # hoy - 30

    def test_el_tope_son_30_dias(self) -> None:
        assert DOWNLOAD_WINDOW_DAYS == 30

    def test_un_rango_dentro_de_la_ventana_no_cambia(self) -> None:
        rango = (date(2026, 9, 20), date(2026, 10, 1))

        assert clamp_download_window(*rango, hoy=self.HOY) == rango

    def test_el_piso_exacto_no_se_recorta(self) -> None:
        assert clamp_download_window(self.PISO, self.HOY, hoy=self.HOY) == (self.PISO, self.HOY)

    def test_un_rango_mas_largo_se_recorta_al_piso(self) -> None:
        assert clamp_download_window(date(2026, 4, 1), self.HOY, hoy=self.HOY) == (self.PISO, self.HOY)

    def test_un_rango_que_cruza_el_piso_conserva_su_final(self) -> None:
        hasta = date(2026, 9, 10)

        assert clamp_download_window(date(2026, 8, 1), hasta, hoy=self.HOY) == (self.PISO, hasta)

    def test_un_rango_enteramente_anterior_no_deja_nada(self) -> None:
        assert clamp_download_window(date(2026, 7, 1), date(2026, 9, 4), hoy=self.HOY) is None


class TestElCsvTraeComoMuchoTreintaDias:
    def test_sin_parametros_trae_hasta_el_dia_30_inclusive(self, client: TestClient, sembrado: None) -> None:
        assert _precios(client) == EN_VENTANA

    def test_pedir_90_dias_devuelve_lo_mismo_que_30(self, client: TestClient, sembrado: None) -> None:
        assert _precios(client, "?dias=90") == EN_VENTANA
        assert _filas(client, "?dias=90") == _filas(client, "?dias=30")

    def test_un_desde_de_hace_seis_meses_devuelve_lo_mismo_que_30(
        self, client: TestClient, sembrado: None
    ) -> None:
        query = f"?desde={_hace(180).isoformat()}&hasta={date.today().isoformat()}"

        assert _precios(client, query) == EN_VENTANA

    def test_la_fecha_mas_vieja_del_archivo_no_pasa_de_30_dias(
        self, client: TestClient, sembrado: None
    ) -> None:
        dias = [date.fromisoformat(fila["dia"]) for fila in _filas(client, "?dias=90")]

        assert min(dias) == _hace(30)

    def test_un_rango_enteramente_viejo_devuelve_solo_el_encabezado(
        self, client: TestClient, sembrado: None
    ) -> None:
        respuesta = client.get(f"/datos.csv?desde={_hace(90).isoformat()}&hasta={_hace(40).isoformat()}")

        assert respuesta.status_code == 200
        assert respuesta.headers["content-type"].startswith("text/csv")
        assert respuesta.text.strip().splitlines() == [ENCABEZADO]

    def test_un_rango_que_cruza_el_borde_trae_solo_su_parte_reciente(
        self, client: TestClient, sembrado: None
    ) -> None:
        query = f"?desde={_hace(60).isoformat()}&hasta={_hace(29).isoformat()}"

        assert _precios(client, query) == {"129", "130"}

    def test_un_pedido_mas_corto_se_respeta(self, client: TestClient, sembrado: None) -> None:
        assert _precios(client, "?dias=7") == {"105"}

    def test_las_columnas_no_cambian_y_no_hay_lineas_de_comentario(
        self, client: TestClient, sembrado: None
    ) -> None:
        lineas = client.get("/datos.csv?dias=90").text.strip().splitlines()

        assert lineas[0] == ENCABEZADO
        assert len(lineas) == 1 + len(EN_VENTANA)
        assert not any(linea.startswith("#") for linea in lineas)


class TestLasVistasSiguenMostrandoElPeriodoPedido:
    """Solo se recorta el archivo. La vista de 90 días cuenta sus cinco
    anuncios, y avisa junto a la descarga que el CSV trae menos."""

    AVISO = "El archivo trae solo los últimos 30 días"

    def test_el_tablero_de_90_dias_cuenta_los_cinco_anuncios(
        self, client: TestClient, sembrado: None
    ) -> None:
        texto = _visible(client.get("/precios?vista=tablero&dias=90").text)

        assert "5 anuncios de los últimos 90 días" in texto

    @pytest.mark.parametrize("vista", ("tablero", "mapa", "explorar"))
    def test_con_90_dias_avisa_junto_a_la_descarga(
        self, client: TestClient, sembrado: None, vista: str
    ) -> None:
        texto = _visible(client.get(f"/precios?vista={vista}&productos=arroz&dias=90").text)

        assert self.AVISO in texto

    @pytest.mark.parametrize("vista", ("tablero", "mapa", "explorar"))
    @pytest.mark.parametrize("dias", (7, 30))
    def test_dentro_de_la_ventana_no_avisa(
        self, client: TestClient, sembrado: None, vista: str, dias: int
    ) -> None:
        texto = _visible(client.get(f"/precios?vista={vista}&productos=arroz&dias={dias}").text)

        assert self.AVISO not in texto

    def test_un_rango_de_fechas_viejo_tambien_avisa(self, client: TestClient, sembrado: None) -> None:
        query = f"desde={_hace(60).isoformat()}&hasta={_hace(10).isoformat()}"

        texto = _visible(client.get(f"/precios?vista=tablero&{query}").text)

        assert self.AVISO in texto

    def test_el_aviso_del_tablero_enlaza_a_uso_de_datos(self, client: TestClient, sembrado: None) -> None:
        texto = client.get("/precios?vista=tablero&dias=90").text

        assert 'href="/uso-de-datos"' in texto


class TestElPieDiceLaReglaEnCorto:
    @pytest.mark.parametrize("ruta", ("/", *RUTAS_DE_TEXTO))
    def test_ya_no_ofrece_los_datos_con_solo_citar(self, client: TestClient, ruta: str) -> None:
        pie = _visible(_pie(client.get(ruta).text))

        assert "Los datos se pueden usar citando la fuente" not in pie
        assert "Uso personal y con cita; uso comercial, con acuerdo." in pie

    @pytest.mark.parametrize("ruta", ("/", *RUTAS_DE_TEXTO))
    def test_la_regla_enlaza_a_uso_de_datos(self, client: TestClient, ruta: str) -> None:
        pie = _pie(client.get(ruta).text)

        regla = re.escape("Uso personal y con cita; uso comercial, con acuerdo.")
        assert re.search(rf'<a[^>]*href="/uso-de-datos"[^>]*>\s*{regla}\s*</a>', pie)


class TestUsoDeDatos:
    def test_los_encabezados_siguen_el_esquema(self, client: TestClient) -> None:
        cuerpo = _cuerpo(client.get("/uso-de-datos").text)

        assert [_visible(h) for h in re.findall(r"<h2[^>]*>(.*?)</h2>", cuerpo, flags=re.S)] == [
            "Qué puedes hacer gratis",
            "Qué necesita un acuerdo",
            "Cómo pedir un acuerdo",
            "Cómo citarlos",
            "Qué trae el archivo",
            "Qué tener en cuenta al usarlos",
        ]

    def test_nombra_el_uso_comercial_y_el_acuerdo_con_leira_studio(self, client: TestClient) -> None:
        texto = _visible(_cuerpo(client.get("/uso-de-datos").text))

        assert "uso comercial" in texto.lower()
        assert "acuerdo previo con Leira Studio" in texto
        for caso in ("Revender", "servicio de pago", "en bloque", "automatizada o masiva"):
            assert caso in texto, caso

    def test_dice_lo_que_sigue_siendo_gratis(self, client: TestClient) -> None:
        texto = _visible(_cuerpo(client.get("/uso-de-datos").text)).lower()

        for esperado in ("personal", "periodístico", "académico", "citando la fuente", "últimos 30 días"):
            assert esperado in texto, esperado

    def test_dice_que_el_historico_completo_va_por_acuerdo(self, client: TestClient) -> None:
        texto = _visible(_cuerpo(client.get("/uso-de-datos").text)).lower()

        assert "histórico completo" in texto

    def test_ya_no_promete_reutilizacion_libre(self, client: TestClient) -> None:
        html = client.get("/uso-de-datos").text
        texto = (_visible(html) + " " + " ".join(re.findall(r'content="([^"]*)"', html))).lower()

        for vieja in ("están abiertos", "datos abiertos", "una aplicación", "reutilizar"):
            assert vieja not in texto, vieja

    def test_conserva_las_columnas_la_cita_y_el_credito_de_la_tasa(self, client: TestClient) -> None:
        html = client.get("/uso-de-datos").text

        for columna in ENCABEZADO.split(","):
            assert f"<code>{columna}</code>" in html, columna
        assert 'href="/datos.csv"' in html
        assert "Fuente: Guani, precios del mercado informal cubano." in _visible(html)
        assert 'href="https://eltoque.com/"' in _cuerpo(html)
        assert "Son anuncios, no ventas" in _visible(html)

    def test_sin_contacto_no_promete_un_canal_que_no_existe(self, client: TestClient) -> None:
        cuerpo = _cuerpo(client.get("/uso-de-datos").text)

        assert 'href="/contacto"' not in cuerpo
        assert "mailto:" not in cuerpo
        assert "El canal de contacto se publicará en este sitio" in _visible(cuerpo)

    def test_con_contacto_enlaza_a_la_pagina_de_contacto(
        self, client: TestClient, con_contacto: None
    ) -> None:
        cuerpo = _cuerpo(client.get("/uso-de-datos").text)

        assert 'href="/contacto"' in cuerpo
        assert "se publicará" not in _visible(cuerpo)
        assert client.get("/contacto").status_code == 200


class TestElRestoDeLoPublicoNoContradiceElEsquema:
    def test_los_terminos_dicen_la_regla_en_corto(self, client: TestClient) -> None:
        texto = _visible(_cuerpo(client.get("/terminos").text))

        assert "reutilizar los datos de Guani citando la fuente" not in texto
        assert "uso comercial" in texto.lower()
        assert "acuerdo previo con Leira Studio" in texto
        assert 'href="/uso-de-datos"' in _cuerpo(client.get("/terminos").text)

    def test_las_preguntas_ya_no_dicen_que_basta_con_citar(self, client: TestClient) -> None:
        texto = _visible(_cuerpo(client.get("/preguntas").text))

        assert "Puedes usarlo citando la fuente" not in texto
        assert "uso comercial" in texto.lower()
        assert "acuerdo" in texto

    def test_la_landing_ya_no_dice_abiertos_para_descargar(self, client: TestClient) -> None:
        texto = _visible(client.get("/").text)

        assert "Abiertos para descargar" not in texto
        assert 'href="/datos.csv"' in client.get("/").text

    @pytest.mark.parametrize("ruta", ("/", *RUTAS_DE_TEXTO))
    def test_donde_se_habla_de_citar_tambien_se_nombra_el_uso_comercial(
        self, client: TestClient, ruta: str
    ) -> None:
        """«Citando la fuente» ya no es un permiso general: donde aparezca,
        la misma página dice que el uso comercial va por acuerdo."""
        texto = _visible(client.get(ruta).text).lower()

        if "citando la fuente" in texto or "con cita" in texto:
            assert "uso comercial" in texto
            assert "acuerdo" in texto

    @pytest.mark.parametrize(
        "ruta",
        (
            "/",
            *RUTAS_DE_TEXTO,
            "/precios?vista=tablero&dias=90",
            "/precios?vista=explorar",
            "/precios?vista=mapa",
        ),
    )
    def test_ninguna_frase_de_datos_abiertos(self, client: TestClient, ruta: str) -> None:
        texto = _visible(client.get(ruta).text).lower()

        for frase in (
            "están abiertos",
            "datos abiertos",
            "abiertos para descargar",
            "descárgalos y úsalos",
            "se pueden usar citando la fuente",
            "libre reutilización",
        ):
            assert frase not in texto, frase

    @pytest.mark.parametrize("ruta", ("/", *RUTAS_DE_TEXTO, "/precios?vista=tablero&dias=90"))
    def test_ni_nombre_de_licencia_ni_jurisdiccion(self, client: TestClient, ruta: str) -> None:
        html = client.get(ruta).text.lower()

        for palabra in (
            "jurisdicción",
            "jurisdiccion",
            "ley aplicable",
            "creative commons",
            "cc by",
            "cc-by",
            "by-nc",
            "odbl",
            "open database",
            "razón social",
        ):
            assert palabra not in html, palabra

    def test_con_contacto_tampoco_hay_licencia_ni_jurisdiccion(
        self, client: TestClient, con_contacto: None
    ) -> None:
        html = client.get("/uso-de-datos").text.lower()

        for palabra in ("jurisdicción", "creative commons", "cc by", "by-nc"):
            assert palabra not in html, palabra
