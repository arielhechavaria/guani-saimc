"""Pie compartido y páginas institucionales de la capa pública (2026-10-05).

Lo que estos casos protegen:

- El pie tiene las columnas del diseño aprobado
  (`docs/prototipos/landing-guani-b.dc.html`) y **ningún enlace muerto**: cada
  `href` interno del pie se pide y tiene que responder 200.
- Cada página de texto existe, lleva su título, su descripción propia, la
  marca que vuelve a `/`, un enlace a `/precios` y el pie.
- `/contacto` y su enlace existen solo si hay un correo o una red configurados;
  la columna «Síguenos», solo con alguna variable puesta.
- Ningún marcador del prototipo, ningún `href="#"`, ningún voseo y ningún
  enlace a la herramienta del operador llegan al visitante.
- El tablero, el mapa y explorar siguen sin este pie (decisión del 2026-09-21).
- La capa pública se firma «Guani, por Leira Studio» (2026-10-05): Leira Studio
  es la marca que firma, nunca una entidad legal ni la titular del «©».

Los enlaces esperados de cada columna están escritos a mano a partir del
diseño, antes de que existiera el módulo `site_pages`.
"""

import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.config import Settings
from saimc.web import site_pages
from saimc.web.app import app
from saimc.web.deps import get_db
from saimc.web.routers import public
from saimc.web.site_pages import build_site_footer

#: Ruta y título de cada página de texto que no depende de la configuración.
PAGINAS = (
    ("/quienes-somos", "Quiénes somos"),
    ("/metodologia", "Cómo calculamos los precios"),
    ("/preguntas", "Preguntas frecuentes"),
    ("/terminos", "Términos y condiciones"),
    ("/privacidad", "Política de privacidad"),
    ("/uso-de-datos", "Uso de los datos"),
)
RUTAS = tuple(ruta for ruta, _titulo in PAGINAS)

#: Lo que el diseño manda en las tres primeras columnas, en orden.
COLUMNA_PRECIOS = (
    ("Tablero de hoy", "/precios?vista=tablero"),
    ("Mapa por provincia", "/precios?vista=mapa"),
    ("Explorar y comparar", "/precios?vista=explorar"),
    ("Descargar datos (CSV)", "/datos.csv"),
)
COLUMNA_GUANI = (
    ("Quiénes somos", "/quienes-somos"),
    ("Cómo calculamos los precios", "/metodologia"),
    ("Preguntas frecuentes", "/preguntas"),
)
COLUMNA_LEGAL = (
    ("Términos y condiciones", "/terminos"),
    ("Política de privacidad", "/privacidad"),
    ("Uso de los datos", "/uso-de-datos"),
)

MARCADORES = ("000", "[TU", "[AÑO]", "[PROVINCIA]", "Cifras de ejemplo")

#: Formas de voseo que no pueden salir: el texto va en español de Cuba, de tú.
VOSEO = re.compile(
    r"\b(entrá|descargá|mirá|abrí|elegí|usá|citá|escribí|leé|andá|hacé|fijate|"
    r"podés|tenés|querés|sabés|necesitás|encontrás|llegás|vos)\b",
    flags=re.I,
)


def _settings(**valores: str) -> Settings:
    """Configuración sin leer ningún `.env`: el del desarrollador no puede
    decidir si un test de enlaces pasa."""
    return Settings(_env_file=None, **valores)  # type: ignore[arg-type, call-arg]


def _visible(html: str) -> str:
    """El texto que lee el visitante: sin estilos, sin scripts y sin etiquetas."""
    sin_bloques = re.sub(r"<(style|script)\b.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", sin_bloques)


def _pie(html: str) -> str:
    """El marcado del pie compartido. Falla si la página no lo trae."""
    encontrado = re.search(r'<footer class="pf".*?</footer>', html, flags=re.S)
    assert encontrado is not None, "la página no trae el pie compartido"
    return encontrado.group(0)


def _enlaces(html: str) -> list[tuple[str, str]]:
    """(texto, href) de cada `<a>` del trozo, con el `&amp;` deshecho."""
    return [
        (re.sub(r"\s+", " ", _visible(cuerpo)).strip(), href.replace("&amp;", "&"))
        for href, cuerpo in re.findall(r'<a\b[^>]*\bhref="([^"]*)"[^>]*>(.*?)</a>', html, flags=re.S)
    ]


def _columna(pie: str, etiqueta: str) -> list[tuple[str, str]]:
    """Los enlaces de la columna cuyo `<nav>` lleva ese `aria-label`."""
    encontrado = re.search(rf'<nav[^>]*aria-label="{etiqueta}"[^>]*>(.*?)</nav>', pie, flags=re.S)
    assert encontrado is not None, f"el pie no trae la columna {etiqueta}"
    return _enlaces(encontrado.group(1))


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
    monkeypatch.setattr(
        public,
        "get_settings",
        lambda: _settings(contact_email="hola@guani.example", link_telegram="https://t.me/guani"),
    )


class TestElPieArmado:
    """`build_site_footer`, sin pasar por HTML."""

    def test_las_tres_columnas_fijas_en_el_orden_del_diseno(self) -> None:
        pie = build_site_footer(_settings(), year=2026)

        assert [c.title for c in pie.columns] == ["Precios", "Guani", "Legal"]
        precios, guani, legal = pie.columns
        assert tuple((e.label, e.href) for e in precios.links) == COLUMNA_PRECIOS
        assert tuple((e.label, e.href) for e in guani.links) == COLUMNA_GUANI
        assert tuple((e.label, e.href) for e in legal.links) == COLUMNA_LEGAL

    def test_contacto_entra_a_la_columna_guani_solo_si_hay_a_donde_escribir(self) -> None:
        sin = build_site_footer(_settings(), year=2026)
        con_correo = build_site_footer(_settings(contact_email="hola@guani.example"), year=2026)
        con_red = build_site_footer(_settings(link_facebook="https://facebook.com/guani"), year=2026)

        assert not sin.has_contact
        assert ("Contacto", "/contacto") not in [(e.label, e.href) for e in sin.columns[1].links]
        for pie in (con_correo, con_red):
            assert pie.has_contact
            assert [(e.label, e.href) for e in pie.columns[1].links][-1] == ("Contacto", "/contacto")

    def test_un_valor_mal_escrito_no_abre_la_pagina_de_contacto(self) -> None:
        mal = _settings(contact_email="sin-arroba", link_facebook="facebook.com/x")

        pie = build_site_footer(mal, year=2026)

        assert not pie.has_contact

    def test_lleva_el_anio_que_se_le_pasa(self) -> None:
        assert build_site_footer(_settings(), year=2031).year == 2031


class TestElPieEnLaLanding:
    def test_trae_las_tres_columnas_con_sus_enlaces(self, client: TestClient) -> None:
        pie = _pie(client.get("/").text)

        assert tuple(_columna(pie, "Precios")) == COLUMNA_PRECIOS
        assert tuple(_columna(pie, "Guani")) == COLUMNA_GUANI
        assert tuple(_columna(pie, "Legal")) == COLUMNA_LEGAL

    def test_trae_la_marca_la_frase_y_el_boton_a_los_precios(self, client: TestClient) -> None:
        pie = _pie(client.get("/").text)

        assert 'aria-label="Guani"' in pie  # el avatar
        assert "Precios del mercado informal cubano, con los anuncios detrás." in pie
        assert ("Ver los precios de hoy", "/precios") in _enlaces(pie)

    def test_la_barra_de_abajo_lleva_el_anio_y_el_credito_a_eltoque(self, client: TestClient) -> None:
        pie = _pie(client.get("/").text)

        assert f"© {date.today().year} Guani" in pie
        assert "Uso personal y con cita; uso comercial, con acuerdo." in pie
        assert ("TRMI de elTOQUE", "https://eltoque.com/") in _enlaces(pie)

    def test_la_landing_no_conserva_el_pie_viejo(self, client: TestClient) -> None:
        texto = client.get("/").text

        assert texto.count("<footer") == 1
        assert "lp-foot" not in texto

    def test_el_avatar_del_pie_no_repite_el_id_del_de_la_portada(self, client: TestClient) -> None:
        ids = re.findall(r'<clipPath id="([^"]+)"', client.get("/").text)

        assert len(ids) == 2
        assert len(set(ids)) == 2

    def test_la_landing_enlaza_a_la_metodologia(self, client: TestClient) -> None:
        cuerpo = client.get("/").text.split("<footer")[0]

        assert 'href="/metodologia"' in cuerpo


def _plano(html: str) -> str:
    """El texto visible con los espacios colapsados, para buscar frases."""
    return re.sub(r"\s+", " ", _visible(html)).strip()


class TestLaFirmaDeMarca:
    """«Guani, por Leira Studio». Leira Studio es una marca, no una sociedad:
    lo que se escribe la nombra como quien hace Guani y nada más."""

    @pytest.mark.parametrize("ruta", ("/", *RUTAS))
    def test_el_pie_dice_guani_por_leira_studio(self, client: TestClient, ruta: str) -> None:
        pie = _pie(client.get(ruta).text)
        marca = re.search(r'<div class="pf-brand">.*?</svg>(.*?)<p class="pf-tag"', pie, flags=re.S)
        assert marca is not None

        assert _plano(marca.group(1)) == "Guani por Leira Studio"

    @pytest.mark.parametrize("ruta", ("/", *RUTAS))
    def test_el_pie_ya_no_dice_abierto_a_secas(self, client: TestClient, ruta: str) -> None:
        pie = _plano(_pie(client.get(ruta).text))

        assert "Abierto y gratis" not in pie
        assert "De acceso libre y gratis." in pie

    @pytest.mark.parametrize("ruta", ("/", *RUTAS))
    def test_el_copyright_no_pone_a_la_marca_como_titular(self, client: TestClient, ruta: str) -> None:
        barra = _plano(_pie(client.get(ruta).text))

        assert f"© {date.today().year} Guani. Un producto de Leira Studio." in barra
        assert not re.search(r"©[^.]*Leira", barra)

    @pytest.mark.parametrize("ruta", ("/", *RUTAS, "/precios"))
    def test_los_metadatos_firman_con_la_marca(self, client: TestClient, ruta: str) -> None:
        texto = client.get(ruta).text

        assert '<meta property="og:site_name" content="Guani"' in texto
        assert re.findall(r'<meta name="author" content="([^"]*)"', texto) == ["Leira Studio"]

    def test_quienes_somos_dice_quien_hace_guani(self, client: TestClient) -> None:
        texto = _plano(client.get("/quienes-somos").text)

        assert "Leira Studio es un estudio de desarrollo de software" in texto
        assert "sistema de precios del mercado informal cubano" in texto

    @pytest.mark.parametrize("ruta", ("/terminos", "/privacidad", "/uso-de-datos"))
    def test_los_textos_legales_dicen_de_quien_es_el_producto(self, client: TestClient, ruta: str) -> None:
        cuerpo = _plano(client.get(ruta).text.split("<footer")[0])

        assert "Guani es un producto de Leira Studio" in cuerpo

    @pytest.mark.parametrize("ruta", ("/", *RUTAS))
    def test_la_grafia_de_la_marca_es_una_sola(self, client: TestClient, ruta: str) -> None:
        texto = client.get(ruta).text

        assert set(re.findall(r"leira\s*studio", texto, flags=re.I)) == {"Leira Studio"}

    @pytest.mark.parametrize("ruta", ("/", *RUTAS))
    def test_no_presenta_a_la_marca_como_entidad_legal(self, client: TestClient, ruta: str) -> None:
        texto = _plano(client.get(ruta).text).lower()

        for palabra in ("empresa registrada", "sociedad", "s.r.l", "mipyme", "razón social"):
            assert palabra not in texto, palabra
        assert not re.search(r"leira studio,? (s\.|inc|ltd|llc)", texto)

    def test_quienes_somos_ya_no_se_presenta_como_proyecto_abierto(self, client: TestClient) -> None:
        texto = client.get("/quienes-somos").text.lower()

        assert "proyecto abierto" not in texto
        assert "sitio abierto" not in texto
        assert "proyecto en fase inicial" in texto


class TestNingunEnlaceMuerto:
    @pytest.mark.parametrize("origen", ("/", *RUTAS))
    def test_cada_enlace_interno_del_pie_responde_200(self, client: TestClient, origen: str) -> None:
        pie = _pie(client.get(origen).text)
        internos = [href for _texto, href in _enlaces(pie) if href.startswith("/")]

        assert len(internos) >= 11  # botón + 4 + 3 + 3
        for href in internos:
            assert client.get(href).status_code == 200, href

    def test_con_contacto_configurado_tampoco_hay_enlaces_muertos(
        self, client: TestClient, con_contacto: None
    ) -> None:
        pie = _pie(client.get("/").text)
        internos = [href for _texto, href in _enlaces(pie) if href.startswith("/")]

        assert "/contacto" in internos
        for href in internos:
            assert client.get(href).status_code == 200, href

    @pytest.mark.parametrize("origen", ("/", *RUTAS))
    def test_ninguna_pagina_sirve_un_enlace_vacio(self, client: TestClient, origen: str) -> None:
        texto = client.get(origen).text

        assert 'href="#"' not in texto
        assert 'href=""' not in texto

    @pytest.mark.parametrize("origen", RUTAS)
    def test_los_enlaces_internos_del_cuerpo_tambien_existen(self, client: TestClient, origen: str) -> None:
        cuerpo = client.get(origen).text.split("<footer")[0]

        for _texto, href in _enlaces(cuerpo):
            if href.startswith("/") and not href.startswith("/static/"):
                assert client.get(href).status_code == 200, href


class TestLasPaginasDeTexto:
    @pytest.mark.parametrize(("ruta", "titulo"), PAGINAS)
    def test_responde_con_su_titulo_y_el_pie(self, client: TestClient, ruta: str, titulo: str) -> None:
        respuesta = client.get(ruta)

        assert respuesta.status_code == 200
        assert f"<title>{titulo} — Guani</title>" in respuesta.text
        assert re.search(rf"<h1[^>]*>\s*{re.escape(titulo)}\s*</h1>", respuesta.text)
        _pie(respuesta.text)

    @pytest.mark.parametrize("ruta", RUTAS)
    def test_la_cabecera_vuelve_a_la_landing_y_lleva_a_los_precios(
        self, client: TestClient, ruta: str
    ) -> None:
        cabecera = client.get(ruta).text.split("<header")[1].split("</header>")[0]

        assert re.search(r'<a[^>]+href="/"[^>]*>.*?Guani.*?</a>', cabecera, flags=re.S)
        assert 'href="/precios"' in cabecera

    def test_cada_pagina_tiene_su_propia_descripcion(self, client: TestClient) -> None:
        descripciones = []
        for ruta in ("/", *RUTAS):
            encontrada = re.search(r'<meta name="description" content="([^"]{40,})"', client.get(ruta).text)
            assert encontrada is not None, ruta
            descripciones.append(encontrada.group(1))

        assert len(set(descripciones)) == len(descripciones)

    @pytest.mark.parametrize(("ruta", "titulo"), PAGINAS)
    def test_lleva_los_metadatos_para_compartir(self, client: TestClient, ruta: str, titulo: str) -> None:
        texto = client.get(ruta).text

        assert f'<meta property="og:title" content="{titulo} — Guani"' in texto
        assert f'<meta property="og:url" content="http://testserver{ruta}"' in texto

    @pytest.mark.parametrize("ruta", RUTAS)
    def test_no_sirve_marcadores_del_prototipo(self, client: TestClient, ruta: str) -> None:
        texto = _visible(client.get(ruta).text)

        for marcador in MARCADORES:
            assert marcador not in texto

    @pytest.mark.parametrize("ruta", ("/", *RUTAS))
    def test_habla_de_tu_no_de_vos(self, client: TestClient, ruta: str) -> None:
        encontrado = VOSEO.search(_visible(client.get(ruta).text))

        assert encontrado is None, encontrado and encontrado.group(0)

    @pytest.mark.parametrize("ruta", RUTAS)
    def test_no_pide_nada_a_una_red_ajena(self, client: TestClient, ruta: str) -> None:
        # `<link rel="canonical">` es una pista para buscadores y es absoluto a
        # propósito: el navegador no pide nada a esa URL (`web/seo.py`).
        texto = re.sub(r'<link rel="canonical"[^>]*>', "", client.get(ruta).text)

        assert not re.search(r'<(link|script|img)[^>]+(href|src)="https?://', texto)

    @pytest.mark.parametrize("ruta", RUTAS)
    def test_no_expone_la_herramienta_del_operador(self, client: TestClient, ruta: str) -> None:
        texto = client.get(ruta).text

        for interna in ("/curation", "/control", "/catalog", "/docs", "/marketing"):
            assert f'href="{interna}' not in texto

    @pytest.mark.parametrize("ruta", RUTAS)
    def test_no_trae_telefonos(self, client: TestClient, ruta: str) -> None:
        """Ni un móvil cubano (8 dígitos que empiezan en 5) ni un `tel:`."""
        texto = client.get(ruta).text

        assert "tel:" not in texto
        assert not re.search(r"(?<!\d)5\d{7}(?!\d)", _visible(texto))

    @pytest.mark.parametrize("ruta", RUTAS)
    def test_con_la_base_vacia_no_afirma_cifras_de_datos(self, client: TestClient, ruta: str) -> None:
        """Estas páginas no consultan la base: no pueden decir cuántos
        anuncios, usuarios o provincias hay."""
        texto = _visible(client.get(ruta).text)

        assert not re.search(r"\d+\s+(anuncios|usuarios|visitas|provincias con)", texto)


class TestLoQueDiceCadaPagina:
    """Lo mínimo que cada texto tiene que decir, y lo que no puede decir."""

    def test_la_metodologia_publica_lo_que_el_sitio_ya_calcula(self, client: TestClient) -> None:
        texto = _visible(client.get("/metodologia").text)

        for esperado in ("mediana", "TRMI", "elTOQUE", "CUP", "WhatsApp", "30 días", "base N"):
            assert esperado in texto, esperado

    def test_la_metodologia_declara_la_unica_reserva_y_su_motivo(self, client: TestClient) -> None:
        """`spec.md` §6.4.1: la capa de detección de coordinación se reserva,
        y eso se dice en público junto con el porqué."""
        texto = _visible(client.get("/metodologia").text).lower()

        assert "coordina" in texto
        assert "no se publican" in texto

    def test_la_privacidad_nombra_lo_que_guarda_el_navegador(self, client: TestClient) -> None:
        texto = client.get("/privacidad").text

        for nombre in ("vista", "provincia", "guani:theme"):
            assert f"<code>{nombre}</code>" in texto, nombre

    def test_la_privacidad_dice_que_no_se_publican_contactos_ni_origen(self, client: TestClient) -> None:
        texto = _visible(client.get("/privacidad").text).lower()

        assert "teléfono" in texto
        assert "no se publica" in texto
        assert "openstreetmap" in texto  # el mapa pide sus teselas a un tercero

    @pytest.mark.parametrize("ruta", ("/terminos", "/uso-de-datos", "/privacidad", "/quienes-somos"))
    def test_los_textos_legales_no_declaran_lo_que_nadie_decidio(self, client: TestClient, ruta: str) -> None:
        texto = _visible(client.get(ruta).text).lower()

        prohibido = (
            "jurisdicción",
            "ley aplicable",
            "tribunales",
            "creative commons",
            "cc-by",
            "cc by",
            "s.a.",
            "s.r.l",
            "llc",
            "fundad",
            "rgpd",
            "gdpr",
        )
        for palabra in prohibido:
            assert palabra not in texto, palabra

    def test_los_terminos_dicen_que_no_es_una_oferta_ni_hay_garantia(self, client: TestClient) -> None:
        texto = _visible(client.get("/terminos").text).lower()

        assert "gratuit" in texto
        assert "garant" in texto
        assert "no vende" in texto
        assert "eltoque" in texto

    def test_el_uso_de_datos_dice_como_citar_y_enlaza_la_descarga(self, client: TestClient) -> None:
        respuesta = client.get("/uso-de-datos")

        assert 'href="/datos.csv"' in respuesta.text
        assert "citando la fuente" in _visible(respuesta.text)
        assert "Guani" in _visible(respuesta.text)

    def test_las_preguntas_son_entre_seis_y_ocho(self, client: TestClient) -> None:
        cuerpo = client.get("/preguntas").text.split("<footer")[0]
        preguntas = re.findall(r"<h2[^>]*>(.*?)</h2>", cuerpo, flags=re.S)

        assert 6 <= len(preguntas) <= 8
        assert all(p.strip().startswith("¿") for p in preguntas)


class TestContacto:
    def test_sin_configuracion_no_existe(self, client: TestClient) -> None:
        assert client.get("/contacto").status_code == 404

    def test_sin_configuracion_el_pie_no_lo_enlaza(self, client: TestClient) -> None:
        for ruta in ("/", *RUTAS):
            assert 'href="/contacto"' not in client.get(ruta).text, ruta

    def test_con_correo_existe_y_lo_muestra(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(public, "get_settings", lambda: _settings(contact_email="hola@guani.example"))

        respuesta = client.get("/contacto")

        assert respuesta.status_code == 200
        assert "<title>Contacto — Guani</title>" in respuesta.text
        cuerpo = respuesta.text.split("<footer")[0]
        assert 'href="mailto:hola@guani.example"' in cuerpo
        assert ("Contacto", "/contacto") in _columna(_pie(respuesta.text), "Guani")

    def test_con_solo_una_red_existe_y_no_inventa_un_correo(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            public, "get_settings", lambda: _settings(link_whatsapp="https://whatsapp.com/channel/abc")
        )

        respuesta = client.get("/contacto")

        assert respuesta.status_code == 200
        cuerpo = respuesta.text.split("<footer")[0]
        assert 'href="https://whatsapp.com/channel/abc"' in cuerpo
        assert "mailto:" not in respuesta.text

    def test_con_configuracion_el_pie_de_todas_lo_enlaza(
        self, client: TestClient, con_contacto: None
    ) -> None:
        for ruta in ("/", *RUTAS, "/contacto"):
            assert ("Contacto", "/contacto") in _columna(_pie(client.get(ruta).text), "Guani"), ruta


class TestLaColumnaDeRedes:
    def test_sin_variables_no_existe(self, client: TestClient) -> None:
        for ruta in ("/", *RUTAS):
            pie = _pie(client.get(ruta).text)
            assert 'aria-label="Redes"' not in pie
            assert "Síguenos" not in pie

    def test_con_variables_trae_solo_lo_configurado(self, client: TestClient, con_contacto: None) -> None:
        for ruta in ("/", "/terminos"):
            redes = _columna(_pie(client.get(ruta).text), "Redes")
            assert redes == [
                ("Telegram", "https://t.me/guani"),
                # El rótulo no es la dirección: a 390 px una dirección real no
                # entra en la columna y se partía a mitad de palabra. La
                # dirección escrita vive en `/contacto`.
                ("Escríbenos", "mailto:hola@guani.example"),
            ]


class TestElTableroSigueSinEstePie:
    @pytest.mark.parametrize(
        "ruta", ("/precios", "/precios?vista=tablero", "/precios?vista=mapa", "/precios?vista=explorar")
    )
    def test_las_vistas_de_precios_no_lo_traen(self, client: TestClient, ruta: str) -> None:
        respuesta = client.get(ruta)

        assert respuesta.status_code == 200
        assert '<footer class="pf"' not in respuesta.text


TROPIPAY = "https://www.tropipay.com/guani"


@pytest.fixture
def sin_qr(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """El QR apunta a un archivo que no existe, sea cual sea el del repo."""
    monkeypatch.setattr(site_pages, "DONATE_QR_DIR", tmp_path)


@pytest.fixture
def con_qr(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "apoyar-qr-clasica.png").write_bytes(b"png")
    (tmp_path / "apoyar-qr-bandec.png").write_bytes(b"png")
    monkeypatch.setattr(site_pages, "DONATE_QR_DIR", tmp_path)


class TestApoyar:
    """`/apoyar` existe si y solo si hay enlace de donación o QR."""

    @pytest.mark.usefixtures("sin_qr")
    def test_sin_configuracion_no_existe_ni_se_enlaza(self, client: TestClient) -> None:
        assert client.get("/apoyar").status_code == 404
        for ruta in ("/", *RUTAS):
            assert 'href="/apoyar"' not in client.get(ruta).text, ruta

    @pytest.mark.usefixtures("sin_qr")
    def test_un_enlace_que_no_es_web_no_cuenta(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(public, "get_settings", lambda: _settings(donate_url="javascript:alert(1)"))
        assert client.get("/apoyar").status_code == 404

    @pytest.mark.usefixtures("sin_qr")
    def test_con_enlace_muestra_el_boton_exacto_y_no_el_qr(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(public, "get_settings", lambda: _settings(donate_url=TROPIPAY))

        respuesta = client.get("/apoyar")

        assert respuesta.status_code == 200
        assert "<title>Apoyar a Guani — Guani</title>" in respuesta.text
        cuerpo = respuesta.text.split("<footer")[0]
        assert f'href="{TROPIPAY}"' in cuerpo
        assert "apoyar-qr" not in cuerpo
        assert ("Apoyar", "/apoyar") in _columna(_pie(respuesta.text), "Guani")

    @pytest.mark.usefixtures("sin_qr")
    def test_el_enlace_aparece_en_el_pie_de_todas_las_paginas(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(public, "get_settings", lambda: _settings(donate_url=TROPIPAY))
        for ruta in ("/", *RUTAS):
            assert ("Apoyar", "/apoyar") in _columna(_pie(client.get(ruta).text), "Guani"), ruta

    @pytest.mark.usefixtures("con_qr")
    def test_solo_con_el_archivo_del_qr_existe_y_lo_muestra_con_alt(self, client: TestClient) -> None:
        respuesta = client.get("/apoyar")

        assert respuesta.status_code == 200
        assert 'src="/static/apoyar-qr-clasica.png"' in respuesta.text
        assert 'src="/static/apoyar-qr-bandec.png"' in respuesta.text
        assert "Tarjeta Clásica" in respuesta.text and "Tarjeta Bandec" in respuesta.text
        assert respuesta.text.count('alt="Código QR') == 2
        assert 'class="pg-btn"' not in respuesta.text

    def test_con_un_solo_archivo_se_muestra_solo_ese_qr(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        (tmp_path / "apoyar-qr-bandec.png").write_bytes(b"png")
        monkeypatch.setattr(site_pages, "DONATE_QR_DIR", tmp_path)

        texto = client.get("/apoyar").text

        assert "apoyar-qr-bandec.png" in texto
        assert "apoyar-qr-clasica.png" not in texto

    @pytest.mark.usefixtures("con_qr")
    def test_no_muestra_tarjetas_ni_paypal(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(public, "get_settings", lambda: _settings(donate_url=TROPIPAY))
        texto = _visible(client.get("/apoyar").text).lower()
        assert "paypal" not in texto
        assert not re.search(r"\d{4}[ -]?\d{4}[ -]?\d{4}", texto)
        assert not VOSEO.search(texto)

    @pytest.mark.usefixtures("sin_qr")
    def test_el_lead_de_contacto_incluye_sugerir_una_idea(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(public, "get_settings", lambda: _settings(contact_email="hola@guani.example"))
        assert "sugerir una idea" in client.get("/contacto").text
