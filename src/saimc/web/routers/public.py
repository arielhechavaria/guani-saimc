"""Vista pública. La primera ruta del proyecto que no es del operador.

`spec.md` §9 pone «Visualización: Prototipo de mapa de calor simple y tarjetas
de producto» dentro del alcance de la **Fase 0**, y era el único ítem de ese
alcance sin construir (`decisions.md`, 2026-08-22). Esto es su primer slice: el
mapa con la cobertura real. Las tarjetas de producto, la banda mín–máx y el
coloreado por precio vienen después, en el orden de `docs/backlog.md`.

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
from saimc.web.product_nav import build_product_nav, resolve_product
from saimc.web.series_view import build_series

router = APIRouter()

# Ventana de las tarjetas. 30 días y no los 7 que usa `/control` por defecto:
# esa vista responde «¿cómo se movió esta semana?» para el operador, y esta
# responde «¿cuánto cuesta?» para alguien que compra. Medido el 2026-08-22, a 7
# días entran 5 celdas y a 30 entran 6, con muestras bastante más grandes
# (Arroz/La Habana pasa de n=2 a n=17). No es un umbral de publicación: es el
# período que la tarjeta declara estar describiendo, y lo dice en pantalla.
_CARD_WINDOW_DAYS = 30


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
    db: Session = Depends(get_db),
) -> HTMLResponse:
    # Solo productos bandera, mismo criterio que `/control`: son los 5 que la
    # Fase 0 se comprometió a medir (§9), y la cola larga son 59 filas en 53
    # productos que no sostienen ninguna afirmación.
    pilot_names = pilot_product_names(db)
    pilot = frozenset(pilot_names)

    # `producto` es un slug de URL; `resolve_product` lo lleva de vuelta al
    # nombre piloto o devuelve None (query string raro → se muestra todo, sin
    # 404). El filtro es una refinación de la vista, no expone nada nuevo:
    # sigue dentro de «tarjetas de producto» del alcance §9.
    selected = resolve_product(pilot_names, producto)

    report = compute_retail_ranges(db, days=_CARD_WINDOW_DAYS)
    ranges = [r for r in report.ranges if r.product_name in pilot]
    if selected is not None:
        ranges = [r for r in ranges if r.product_name == selected]
    cards = build_cards(ranges)

    # Misma ventana que las tarjetas a propósito: el gráfico tiene que describir
    # exactamente la muestra que la tarjeta resume, no otra. Dos ventanas
    # distintas en la misma página harían que el rango del titular no coincida
    # con lo que el usuario ve dibujado.
    observations = compute_retail_observations(db, days=_CARD_WINDOW_DAYS)
    obs = [o for o in observations.observations if o.product_name in pilot]
    if selected is not None:
        obs = [o for o in obs if o.product_name == selected]
    series = build_series(obs, window_days=_CARD_WINDOW_DAYS, hoy=date.today())

    return request.app.state.templates.TemplateResponse(
        request,
        "public_home.html",
        {
            "map": build_map(_rows_by_province(db), _published_provinces(db)),
            "cards": cards,
            "series": series,
            "window_days": _CARD_WINDOW_DAYS,
            "product_nav": build_product_nav(pilot_names, selected),
            "selected_product": selected,
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
