# Prompt para Claude Code — aplicar rediseño de la home pública SAIMC

Contexto: se diseñaron 3 direcciones visuales para `/` (home pública) del repo
`guani-saimc`, hechas sobre datos reales del backend (mapa de provincias,
tarjetas de precio, series de anuncios). Elegí la opción **[COMPLETAR: 1a / 1b / 1c]**
del archivo `Home Pública SAIMC.dc.html`. Portala a las plantillas Jinja reales.

## Archivos de referencia (adjuntos)
- `Home Pública SAIMC.dc.html` — las 3 opciones completas en HTML/CSS inline (mirar solo la sección `id="OPCION_ELEGIDA"`).
- `MapaCuba.dc.html` — el mapa de cobertura reconstruido, con 3 variantes de color (paper/dark/wire) y hover por provincia.
- `saimc-data.js` — datos de muestra usados para maquetar (mismo shape que ya devuelve `build_map`/`build_cards`/`build_series`).

## Qué cambia en el repo real
1. **`src/saimc/web/templates/public_home.html`**: reemplazar el `<style>` del bloque `head` y el markup de `content` por el layout de la opción elegida, manteniendo los `{% for %}` / `{{ }}` existentes sobre `map`, `cards`, `series`, `window_days` — no inventar nuevos campos, son los mismos objetos que ya arma `map_view.py`/`card_view.py`/`series_view.py`.
2. **Paleta**: nuevos tokens de color (reemplazan al teal `#00696e` como acento único):
   - Baltic Blue `#05668d` — estructura, cifras de precio, acento primario.
   - Teal `#028090` — relleno del mapa / dato.
   - Icy Aqua `#C1F2E8` — acento luminoso sobre fondo oscuro.
   - Ash Grey `#9AAAA6` — sin datos, líneas finas.
   - Muted Olive `#AEC092` — línea de mediana en los gráficos de serie.
   Definir como custom properties CSS en `saimc-material.css` (o en el `<style>` de `public_home.html` si se mantiene la convención de "pocas reglas, vive con la vista").
3. **Tipografía**: se usó Alice (serif, Google Fonts) para titulares y cifras, sobre la Roboto/Barlow existente para el resto. **Auto-hospedar** el woff2 (~40 KB) en `static/` — el proyecto no permite CDN en runtime (ver `docs/decisions/001-stack-python-unico.md`). No dejar el `<link>` a fonts.googleapis.com en producción.
4. **Mapa SVG**: la geometría de `map_cuba.py` no cambia. Solo se reestilizan los `fill`/`stroke` de `.m3-map-province` según el estado (con/sin datos) usando la nueva paleta, y se agregan los paneles flotantes de leyenda/concentración de muestra si se elige la opción 1a o 1c (son `<div>` con `position:absolute` sobre el contenedor del SVG, no requieren JS).
5. **Sin librerías nuevas**: todo sigue siendo SVG inline generado por Jinja, sin ECharts/MapLibre — se evaluaron pero no se justifican dado el peso y la restricción de "sin CDN, conexión lenta" del proyecto.

## Verificación
- Confirmar que `map.covered`, `map.provinces`, `cards`, `series` siguen recibiendo los mismos objetos (no romper `public.py`).
- Revisar contraste de texto sobre el fondo oscuro nuevo (`#052030`): las etiquetas secundarias deben usar `rgba(242,242,243,.6)` o más, no `.4` (falla WCAG AA en texto pequeño).
- Mobile: las plantillas actuales no tienen breakpoints; si se porta el layout de 3 columnas de "Precios observados", agregar un `@media` a una columna bajo ~640px.
