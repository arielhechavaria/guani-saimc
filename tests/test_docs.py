"""Gestor de documentos (`/docs`).

Lo que más importa acá no es que renderice: es que el slug de la URL **nunca**
alcance el filesystem. El catálogo de `docs_catalog.py` es una lista cerrada y
la resolución es un lookup de diccionario, así que ninguna secuencia de escape
tiene por dónde entrar. Los casos de `TestNoHayTraversal` fijan esa propiedad.
"""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.web import docs_catalog
from saimc.web.app import MARKED_VERSION, app
from saimc.web.auth import require_operator
from saimc.web.deps import get_db


@pytest.fixture
def client(session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    monkeypatch.setenv("SAIMC_INBOX_DIR", str(tmp_path / "inbox"))
    monkeypatch.setenv("SAIMC_RAW_STORE_DIR", str(tmp_path / "raw"))
    get_settings.cache_clear()

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[require_operator] = lambda: None
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture
def sin_auth(session: Session) -> Iterator[TestClient]:
    """Cliente SIN el override de `require_operator`, para comprobar el cierre."""
    def _get_db_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class TestCatalogoCerrado:
    def test_todo_documento_del_catalogo_existe_en_el_repo(self) -> None:
        """Si alguien renombra un documento sin tocar el catálogo, esto avisa.
        La vista degrada a «no está en el disco», pero eso es una red de
        seguridad, no el estado esperado."""
        faltantes = [e.relative_path for e in docs_catalog.CATALOG if not e.path.is_file()]

        assert faltantes == []

    def test_los_slugs_no_se_repiten(self) -> None:
        slugs = [e.slug for e in docs_catalog.CATALOG]

        assert len(slugs) == len(set(slugs))

    def test_la_raiz_del_repo_se_resuelve_por_archivo_no_por_cwd(self) -> None:
        """`REPO_ROOT` sale de `__file__`: uvicorn puede lanzarse desde
        cualquier directorio y el gestor tiene que seguir encontrando `docs/`."""
        assert (docs_catalog.REPO_ROOT / "pyproject.toml").is_file()

    def test_find_devuelve_none_para_un_slug_desconocido(self) -> None:
        assert docs_catalog.find("no-existe") is None


class TestNoHayTraversal:
    """El slug es clave de diccionario, nunca un componente de ruta."""

    @pytest.mark.parametrize(
        "slug",
        [
            "../../etc/passwd",
            "..%2f..%2fetc%2fpasswd",
            "....//....//etc/passwd",
            "/etc/passwd",
            "..\\..\\windows\\system32",
            ".env",
            "docs/spec",
        ],
    )
    def test_ningun_intento_de_escape_devuelve_contenido(
        self, client: TestClient, slug: str
    ) -> None:
        vista = client.get(f"/docs/{slug}")
        crudo = client.get(f"/docs/{slug}/raw")

        assert vista.status_code in (404, 400)
        assert crudo.status_code in (404, 400)

    def test_el_env_no_es_alcanzable_ni_estando_en_el_repo(self, client: TestClient) -> None:
        """`.env` vive en la raíz del repositorio y tiene la clave de WAHA y la
        contraseña de curaduría. Nunca estuvo en el catálogo y no hay slug que
        lo alcance."""
        assert client.get("/docs/.env/raw").status_code in (404, 400)
        assert all(e.relative_path != ".env" for e in docs_catalog.CATALOG)


class TestSwaggerApagado:
    """`/docs` era el Swagger que FastAPI monta solo, servido sin auth sobre
    0.0.0.0:8000 y publicando el mapa entero de endpoints, `/webhooks/whatsapp`
    incluido. Se apagó al tomar la ruta (docs/decisions.md, 2026-08-30)."""

    def test_openapi_no_se_publica(self, sin_auth: TestClient) -> None:
        assert sin_auth.get("/openapi.json").status_code == 404

    def test_redoc_no_se_publica(self, sin_auth: TestClient) -> None:
        assert sin_auth.get("/redoc").status_code == 404

    def test_docs_ya_no_es_la_consola_de_api(self, client: TestClient) -> None:
        texto = client.get("/docs").text

        assert "swagger-ui" not in texto.lower()
        assert "Gestor de documentos" in texto


class TestIndice:
    def test_lista_los_documentos_del_catalogo(self, client: TestClient) -> None:
        texto = client.get("/docs").text

        assert "Gestor de documentos" in texto
        for entry in docs_catalog.CATALOG:
            assert entry.title in texto
            assert f'href="/docs/{entry.slug}"' in texto

    def test_muestra_el_tamano_real_de_cada_documento(self, client: TestClient) -> None:
        """El índice tiene que decir cuánto pesa antes de abrirlo: `decisions.md`
        son 300+ KB y en conexión lenta eso importa."""
        texto = client.get("/docs").text
        spec = docs_catalog.stat_document(docs_catalog.BY_SLUG["spec"])

        assert spec.lines > 1000
        assert f"{spec.kilobytes} KB" in texto

    def test_marca_lo_congelado_como_congelado(self, client: TestClient) -> None:
        """El pitch no rige (CLAUDE.md). Tiene que verse en la lista, no
        descubrirse leyéndolo."""
        texto = client.get("/docs").text

        assert docs_catalog.BY_SLUG["pitch"].standing == "congelado"
        assert "congelado" in texto


class TestLectura:
    def test_la_vista_de_un_documento_carga(self, client: TestClient) -> None:
        response = client.get("/docs/requisitos")

        assert response.status_code == 200
        assert "Inventario de requisitos funcionales" in response.text

    def test_la_vista_no_incrusta_el_markdown_lo_pide_al_endpoint_crudo(
        self, client: TestClient
    ) -> None:
        """El documento viaja por `/docs/{slug}/raw`, no incrustado en el HTML:
        así no hay que escapar `</script>` ni duplicar 300 KB en la página."""
        texto = client.get("/docs/requisitos").text

        assert 'fetch("/docs/requisitos/raw"' in texto

    def test_el_crudo_devuelve_el_archivo_tal_cual(self, client: TestClient) -> None:
        entry = docs_catalog.BY_SLUG["spec-map"]
        response = client.get("/docs/spec-map/raw")

        assert response.status_code == 200
        assert response.text == entry.path.read_text(encoding="utf-8")

    def test_un_slug_desconocido_da_404(self, client: TestClient) -> None:
        assert client.get("/docs/inventado").status_code == 404
        assert client.get("/docs/inventado/raw").status_code == 404

    def test_el_runbook_de_h0_esta_en_el_catalogo_y_se_lee(
        self, client: TestClient
    ) -> None:
        """El procedimiento para medir H0 vive en `/docs`, no en un mensaje
        suelto: el operador lo sigue cada vez que corre la medición."""
        assert docs_catalog.BY_SLUG["runbook-h0"].standing == "rige"

        response = client.get("/docs/runbook-h0")

        assert response.status_code == 200
        assert "Runbook — Medir H0" in response.text

    def test_el_404_es_una_pagina_con_menu_no_un_json(self, client: TestClient) -> None:
        """Un slug errado no puede dejar al operador en un callejón: la vista
        conserva la navegación y ofrece la lista de lo que sí existe."""
        response = client.get("/docs/inventado")

        assert response.status_code == 404
        assert "application/json" not in response.headers["content-type"]
        assert "m3-topnav" in response.text
        assert 'href="/docs/spec"' in response.text

    def test_ofrece_descarga_e_impresion(self, client: TestClient) -> None:
        """Las dos salidas que pidió el operador: `.md` y PDF por el diálogo de
        impresión del navegador."""
        texto = client.get("/docs/spec").text

        assert 'download="spec.md"' in texto
        assert "window.print()" in texto

    def test_degrada_sin_javascript(self, client: TestClient) -> None:
        """El renderer es del lado del cliente, así que sin JS hay que dejar
        una salida: el archivo crudo, que es el mismo contenido sin formato."""
        texto = client.get("/docs/spec").text

        assert "<noscript>" in texto
        assert 'href="/docs/spec/raw"' in texto


class TestCierrePorAuth:
    """El catálogo incluye el documento de inversión y el log de decisiones.
    Ninguna de las tres rutas puede quedar abierta (docs/decisions.md,
    2026-08-30)."""

    @pytest.mark.parametrize("ruta", ["/docs", "/docs/spec", "/docs/spec/raw"])
    def test_sin_credenciales_no_se_lee_nada(self, sin_auth: TestClient, ruta: str) -> None:
        response = sin_auth.get(ruta)

        assert response.status_code == 401
        assert "Especificación" not in response.text


class TestMarkedVendorizado:
    def test_se_sirve_desde_static(self, client: TestClient) -> None:
        response = client.get("/static/marked.umd.js")

        assert response.status_code == 200
        assert f"marked v{MARKED_VERSION}" in response.text

    def test_la_vista_no_pide_el_renderer_a_la_red(self, client: TestClient) -> None:
        """Mismo criterio que el resto de los assets: conexión lenta e
        intermitente, nada de CDN."""
        texto = client.get("/docs/spec").text

        assert "/static/marked.umd.js" in texto
        assert "cdn.jsdelivr.net" not in texto
        assert "unpkg.com" not in texto

    def test_la_version_del_cache_busting_es_la_del_archivo_servido(
        self, client: TestClient
    ) -> None:
        expuesta = app.state.templates.env.globals["marked_version"]()

        assert expuesta == MARKED_VERSION
        assert f"marked v{expuesta}" in client.get("/static/marked.umd.js").text


class TestBuscadorFunciona:
    """El filtro esconde tarjetas con `el.hidden = true`, y eso solo funciona
    si la hoja de estilos lo respeta.

    `[hidden] { display: none }` viene del navegador, pero cualquier regla de
    autor con `display` le gana — y `.docs-card` declara `display: block`. Sin
    una regla propia, escribir en la caja marcaba las tarjetas como ocultas y
    seguían visibles: el buscador parecía roto aunque el JS estuviera bien.

    Es la misma clase de bug que el subconjunto de la fuente de iconos: no se
    ve en el HTML renderizado, así que se prueba sobre el asset.
    """

    def _css(self) -> str:
        """La hoja SIN comentarios: la regla tiene que existir de verdad, no
        estar nombrada en una explicación."""
        crudo = Path("src/saimc/web/static/saimc-material.css").read_text(
            encoding="utf-8"
        )
        return re.sub(r"/\*.*?\*/", "", crudo, flags=re.DOTALL)

    def test_la_hoja_de_estilos_hace_efectivo_el_atributo_hidden(self) -> None:
        css = self._css()

        assert "[hidden]" in css, (
            "sin una regla propia para [hidden], cualquier `display` de autor "
            "deja visible lo que el JS marcó como oculto"
        )

    def test_la_regla_gana_sobre_el_display_de_autor(self) -> None:
        css = self._css()
        bloque = css[css.index("[hidden]") : css.index("[hidden]") + 120]

        assert "display" in bloque and "none" in bloque
        assert "!important" in bloque, (
            "sin !important la regla empata en especificidad con .docs-card y "
            "gana la que esté después en el archivo — frágil"
        )


class TestAgrupadoYReciente:
    """La lista abre mostrando lo último tocado, agrupado por vigencia.

    El orden del catálogo en `docs_catalog.py` es deliberado (primero lo que
    rige, al final lo congelado) y se conserva como orden de GRUPOS. Lo que
    cambia es adentro de cada grupo: lo más recién modificado primero, que es
    lo que el operador viene a buscar cuando abre la vista después de generar
    un documento.
    """

    def test_los_grupos_van_en_orden_de_vigencia(self) -> None:
        grupos = docs_catalog.grouped_stats()

        assert [g.standing for g in grupos] == ["rige", "interno", "congelado"]

    def test_dentro_de_un_grupo_lo_mas_reciente_va_primero(self) -> None:
        grupos = docs_catalog.grouped_stats()
        rige = next(g for g in grupos if g.standing == "rige")

        fechas = [s.modified for s in rige.stats if s.modified is not None]
        assert fechas == sorted(fechas, reverse=True)

    def test_todo_documento_del_catalogo_cae_en_algun_grupo(self) -> None:
        grupos = docs_catalog.grouped_stats()
        agrupados = sum(len(g.stats) for g in grupos)

        assert agrupados == len(docs_catalog.CATALOG)

    def test_un_grupo_sin_documentos_no_se_lista(self) -> None:
        """No hay encabezados vacíos: si algún día no queda nada congelado,
        ese grupo simplemente no aparece."""
        grupos = docs_catalog.grouped_stats()

        assert all(g.stats for g in grupos)

    def test_la_vista_muestra_los_encabezados_de_grupo(
        self, client: TestClient
    ) -> None:
        texto = client.get("/docs").text

        assert "Rige" in texto
        assert "Congelado" in texto

    def test_el_documento_mas_reciente_aparece_antes_que_uno_viejo(
        self, client: TestClient
    ) -> None:
        """La prueba de la promesa: lo último generado se ve primero."""
        grupos = docs_catalog.grouped_stats()
        rige = next(g for g in grupos if g.standing == "rige")
        primero, ultimo = rige.stats[0], rige.stats[-1]

        texto = client.get("/docs").text

        assert texto.index(primero.entry.title) < texto.index(ultimo.entry.title)
