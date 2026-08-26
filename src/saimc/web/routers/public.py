"""Vista pública. La primera ruta del proyecto que no es del operador.

`spec.md` §9 pone «Visualización: Prototipo de mapa de calor simple y tarjetas
de producto» dentro del alcance de la **Fase 0** (`decisions.md`, 2026-08-22).
Hoy `GET /` tiene: mapa de cobertura por provincia (§9.0), tarjetas de rango
minorista con muestra visible, gráfico de evolución, y filtros por producto,
provincia y ventana de días vía query string (`?producto=`, `?provincia=`,
`?dias=`, 2026-08-26). El coloreado del mapa por precio y los semáforos siguen
bloqueados por el umbral sin calibrar (§12.6).

Sin `require_operator` a propósito: es la capa pública. Y sin identificador de
usuario ni dato de contacto en ninguna respuesta — `CLAUDE.md` fija que el
identificador de usuario origen nunca se expone acá.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from datetime import date

from saimc.cells import compute_retail_observations, compute_retail_ranges
from saimc.models import ContentType, PriceHistory, ProcessingStatus, Province, RawIngestion
from saimc.progress import pilot_product_names
from saimc.web.card_view import build_cards
from saimc.web.deps import get_db
from saimc.web.map_view import build_map
from saimc.web.product_nav import (
    build_product_nav,
    build_province_nav,
    build_window_nav,
    resolve_days,
    resolve_product,
    resolve_province,
)
from saimc.web.series_view import build_series

router = APIRouter()

# La ventana de tarjetas y series la elige el visitante con `?dias=` entre
# 7/30/90 (`product_nav.WINDOW_CHOICES`); el default y su motivo viven en
# `product_nav.DEFAULT_WINDOW_DAYS`.


def _rows_by_province(session: Session) -> dict[str, int]:
    """Observaciones por provincia, sobre todo el histórico.

    Sin ventana temporal: con 225 filas en dos meses, recortar a los últimos N
    días dejaría el mapa casi vacío y no diría nada más honesto. La ventana se
    vuelve una pregunta real cuando exista volumen que la justifique, y ahí hay
    que elegir el N con una medición, no de memoria.
    """
    return {
        name: count
        for name, count in session.execute(
            select(Province.name, func.count())
            .join(PriceHistory, PriceHistory.province_id == Province.id)
            .group_by(Province.name)
        )
    }


def _all_province_names(session: Session) -> list[str]:
    """Los 16 nombres de provincia, para resolver `?provincia=` contra un
    conjunto estable: una provincia elegida que hoy no tiene dato piloto sigue
    resolviéndose y muestra su aviso, en vez de ignorarse en silencio."""
    return list(session.execute(select(Province.name)).scalars())


def _published_provinces(session: Session) -> frozenset[str]:
    """Las provincias que alguien decidió publicar (`spec.md` §9.0).

    Sale de la base y no de una constante: §9.0 fija como restricción vinculante
    que «la incorporación de una provincia debe ser configuración, no
    desarrollo». Un `frozenset` de nombres en código obligaría a un deploy para
    publicar una provincia, que es exactamente lo que la sección prohíbe.
    """
    return frozenset(
        session.execute(
            select(Province.name).where(Province.is_published.is_(True))
        ).scalars()
    )


@router.get("/", response_class=HTMLResponse)
def public_home(
    request: Request,
    producto: str | None = None,
    provincia: str | None = None,
    dias: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    # Solo productos bandera, mismo criterio que `/control`: son los 5 que la
    # Fase 0 se comprometió a medir (§9), y la cola larga son 59 filas en 53
    # productos que no sostienen ninguna afirmación.
    pilot_names = pilot_product_names(db)
    pilot = frozenset(pilot_names)

    # `producto`, `provincia` y `dias` son query string; cada uno resuelve a un
    # valor válido o al default (query raro → se muestra todo con la ventana por
    # defecto, sin 404). Los filtros son una refinación de la vista, no exponen
    # nada nuevo: siguen dentro de «tarjetas de producto» del alcance §9.
    selected = resolve_product(pilot_names, producto)
    selected_province = resolve_province(_all_province_names(db), provincia)
    window = resolve_days(dias)

    report = compute_retail_ranges(db, days=window)
    pilot_ranges = [r for r in report.ranges if r.product_name in pilot]

    # La barra de provincia lista las que tienen dato piloto en esta ventana; si
    # la elegida quedó afuera (sin dato en la ventana), se agrega igual para que
    # su chip siga visible y se pueda cambiar.
    province_names = sorted({r.province_name for r in pilot_ranges if r.province_name})
    if selected_province and selected_province not in province_names:
        province_names = sorted({*province_names, selected_province})

    ranges = pilot_ranges
    if selected is not None:
        ranges = [r for r in ranges if r.product_name == selected]
    if selected_province is not None:
        ranges = [r for r in ranges if r.province_name == selected_province]
    cards = build_cards(ranges)

    # Misma ventana que las tarjetas a propósito: el gráfico tiene que describir
    # exactamente la muestra que la tarjeta resume, no otra. Dos ventanas
    # distintas en la misma página harían que el rango del titular no coincida
    # con lo que el usuario ve dibujado.
    observations = compute_retail_observations(db, days=window)
    obs = [o for o in observations.observations if o.product_name in pilot]
    if selected is not None:
        obs = [o for o in obs if o.product_name == selected]
    if selected_province is not None:
        obs = [o for o in obs if o.province_name == selected_province]
    series = build_series(obs, window_days=window, hoy=date.today())

    prov = selected_province or ""
    return request.app.state.templates.TemplateResponse(
        request,
        "public_home.html",
        {
            "map": build_map(_rows_by_province(db), _published_provinces(db)),
            "cards": cards,
            "series": series,
            "window_days": window,
            "product_nav": build_product_nav(
                pilot_names, selected, window, selected_province=prov
            ),
            "province_nav": build_province_nav(
                province_names,
                selected_province,
                selected_product=selected or "",
                selected_days=window,
            ),
            "window_nav": build_window_nav(
                window, selected, selected_province=prov
            ),
            "selected_product": selected,
            "selected_province": selected_province,
        },
    )


@router.get("/anuncio/{ingestion_id}", response_class=HTMLResponse)
def public_evidence(
    ingestion_id: uuid.UUID, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Transparency Layer (§10.6, mitigación 2): «el anuncio y su contenido
    económico, no la identidad del anunciante».

    Solo texto. Una captura de imagen trae nombre del remitente y a veces
    teléfono en los propios píxeles (docs/spec-map.md, contradicción 1); el
    texto pegado no —medido contra las 143 filas curadas de texto en
    producción, 2026-08-25: ninguna contiene un patrón de teléfono— y ya no
    hay forma de redactar una imagen sin OCR/ML, fuera de alcance en Fase 0
    (CLAUDE.md, decisión 2). `channel_id`/`source_reference` nunca se leen acá:
    el identificador de origen no se expone en la capa pública (CLAUDE.md).

    404 idéntico si no existe, si es imagen o si nunca se curó: distinguir
    "existe pero es imagen" de "no existe" filtraría información por el
    camino que la mitigación 2 quiere cerrado.
    """
    ingestion = db.get(RawIngestion, ingestion_id)
    if (
        ingestion is None
        or ingestion.content_type != ContentType.TEXT
        or ingestion.processing_status != ProcessingStatus.CURATED
    ):
        raise HTTPException(status_code=404, detail="No hay anuncio público con ese id")

    return request.app.state.templates.TemplateResponse(
        request,
        "public_evidence.html",
        {
            "original_text": ingestion.original_text,
            "captured_day": ingestion.captured_at.date(),
        },
    )
