# Handoff: Home pública de SAIMC (mapa a pantalla completa)

## Overview
Rediseño de la home pública de SAIMC (`/`), hoy un panel tipo BI. El nuevo diseño hace del mapa de Cuba el lienzo completo de la pantalla: se eligen productos con chips flotantes, el mapa se colorea por precio, y aparecen paneles flotantes con el histórico. Click en una provincia abre el detalle con la serie completa y el enlace al anuncio que respalda cada punto.

## About the design files
Los archivos de esta carpeta son **referencias de diseño hechas en HTML** (un "Design Component" de la herramienta de diseño, no un framework de producción): `SAIMC Home.dc.html` es el prototipo interactivo completo, y `province-paths.js` / `saimc-mock-data.js` son datos de apoyo (geometría real del mapa y datos de precio **sintéticos**, no reales). Nada de este HTML/JS debe copiarse literal al repo — la tarea es **recrear el diseño dentro del stack real del proyecto**: FastAPI + Jinja2, render en servidor, HTMX donde haga falta interacción sin recargar toda la página, y el CSS propio `saimc-material.css` / `saimc-public.css` con sus tokens `--m3-*` / `--ph-*`. El repo ya tiene el módulo de geometría (`src/saimc/web/map_cuba.py`) y el de series (`src/saimc/web/series_view.py`) — usar esos, no los de esta carpeta.

## Fidelity
**Alta fidelidad** en paleta, tipografía, espaciado y estructura de interacción (los colores y tokens son los reales del repo). **Los datos de precio son mock** — al implementar, conectar a `card_view.py` / `series_view.py` / `map_view.py` reales. La lógica de estado (qué producto está activo, cuál enfocado, qué provincia seleccionada) está pensada para expresarse como parámetros de query string + un poco de JS/HTMX, no como una SPA — ver "State management".

## Screens / views
Un solo archivo con estados interactivos, no pantallas separadas:

1. **Estado inicial** — nada prendido: mapa gris uniforme, chips apagados, burbuja de ayuda flotante ("Elegí un producto arriba para ver dónde está más caro y más barato.").
2. **Un producto activo** — mapa coloreado por precio (escala secuencial de un solo tono, claro=barato / oscuro=caro), un panel flotante con rango nacional + mini-gráfico, leyenda de escala abajo a la izquierda.
3. **Varios productos activos** — pila de paneles. Dos formas de convivencia, intercambiables con el tweak `panelLayout` del prototipo:
   - `accordion` (default): cada producto es una barra colapsada; tocarla la expande (con gráfico) y colapsa las demás. El mapa sigue siempre al panel **enfocado** (no al último tocado necesariamente — foco y expansión son el mismo estado).
   - `grid`: todos los paneles se ven a la vez, chicos, sin gráfico, solo rango + conteo. Tocar una tarjeta la marca como "enfocada" (anillo de color) y esa es la que colorea el mapa — no hace falta expandir nada.
4. **Detalle de provincia** — click en cualquier provincia (con o sin producto activo) abre una hoja: bottom-sheet en mobile, drawer lateral derecho en desktop. Lista los 6 productos; los que tienen dato muestran rango + mini-serie con puntos-enlace al anuncio; los que no, "todavía no hay dato acá".
5. **Dato escaso** — tweak `sampleData: "escaso"` simula arranque en frío: cada producto queda con cobertura de una sola provincia, el resto del mapa gris. Usar como referencia de cómo se ve la cobertura real incompleta (que es el caso normal, no la excepción).

## Interactions & behavior
- **Chip de producto** (toggle): tocar prende/apaga. Al prender, ese producto pasa a ser el "enfocado" (colorea el mapa). Al apagar el enfocado, el foco pasa al último producto que quedó activo (o a ninguno).
- **Panel colapsado → tocar header** (modo accordion): pone ese producto como enfocado (expande su panel, colapsa los demás, recolorea el mapa). No hay animación de alto perfecto en el prototipo (limitación de la herramienta) — en producción, animar `max-height`/`grid-template-rows` en la expansión.
- **Cerrar panel (×)**: apaga el producto (mismo efecto que destocar el chip).
- **Click en tarjeta de grilla** (modo grid): la marca como enfocada; no expande nada más.
- **Click en provincia**: abre la hoja de detalle para esa provincia. Funciona con o sin producto activo.
- **Cerrar hoja**: click en el botón × o en el fondo oscurecido.
- **Punto del mini-gráfico**: si la evidencia es pública, es un enlace a `/anuncio/<evidence_id>` (mismo patrón que `series_view.py` ya implementa); si no, no es clicable.
- **Transiciones**: color de provincia anima con `transition: fill .5s ease`; chips y paneles entran con una animación breve de aparición (opacity+translateY, ~350ms, `cubic-bezier(.2,.8,.2,1)`); puntos del gráfico aparecen en cascada (~70ms de diferencia entre uno y el siguiente). Todo debe respetar `prefers-reduced-motion: reduce` (desactivar duraciones).
- **Responsive**: el prototipo decide mobile vs. desktop por ancho de ventana (corte en 900px) — en Jinja2 esto se resuelve con CSS puro (flex-wrap, `grid-template-columns` fluido) en vez de JS, ya que el server no conoce el viewport. La pila de paneles va abajo en mobile y a la derecha en desktop; la hoja de detalle sube desde abajo en mobile y entra desde la derecha en desktop.

## State management
Para mantenerlo sin SPA (mandato del proyecto), expresar como querystring + fragmentos HTMX:
- `?productos=aceite,arroz` — productos activos, en orden de activación.
- `?foco=aceite` — producto que colorea el mapa / está expandido.
- `?vista=grid|accordion` — layout de paneles (podría ser fijo, no hace falta que el usuario lo elija).
- `?provincia=la-habana` — abre la hoja de detalle (reusa el slug de `product_nav.py`).
- Cada toggle de chip, foco de panel o click de provincia es un link/form HTMX (`hx-get` a `/` con el querystring actualizado, `hx-target` en el fragmento que corresponda: mapa, pila de paneles, hoja de detalle) — no requiere JS a mano salvo abrir/cerrar la hoja con una transición CSS.

## Design tokens
Tomados literalmente de `src/saimc/web/design_tokens.css` (ya generan `saimc-public.css`) — no inventar nuevos:
- `--m3-bg: #f2f6f6`, `--m3-surface: #ffffff`, `--m3-surface-container-high: #e3e9ea`, `--m3-outline-variant: #c4cfcf`, `--m3-on-surface: #191c1c`, `--m3-on-surface-variant: #3f4949`.
- `--ph-accent: #05668d` (wordmark, acentos de la capa pública), `--ph-font-heading: "Alice", Georgia, serif` (titulares, cifras grandes), `--m3-font: Roboto, …` (cuerpo).
- `--ph-elev: 0 1px 2px rgba(5,32,48,.08), 0 4px 14px rgba(5,32,48,.06)` para tarjetas/paneles flotantes.
- Escala de precio (nueva, no existía): un solo tono OKLCH por producto, interpolando `L` de 0.88→0.40 y `C` de 0.06→0.11 según el percentil de precio; el hue de cada producto está fijo en el prototipo (aceite 233, arroz 40, pollo 176, azúcar 300, leche 96, cerdo 260) — elegidos por estar lejos de rojo (~25) y verde (~145), reservados a error/éxito en el esquema M3 del repo.
- Radios: `16px` paneles, `20px` hoja de detalle (esquinas superiores en mobile), `999px` chips.
- Provincia sin dato: `#e3e9ea` relleno / `#c4cfcf` borde (idéntico a `.ph-map-province.is-sin-datos` que ya existe en `public_home.html`).
- Provincia "en recolección" (dato aún no publicado): mismo tono del producto pero borde punteado y opacidad ~0.68 — antes esto usaba `--ph-accent-2` fijo; ahora es el tono del producto enfocado, más consistente con la leyenda.

## Assets
- Geometría de las 16 provincias: real, tomada de `src/saimc/web/map_cuba.py` (`PROVINCE_PATHS`, ya generado del dataset Natural Earth de dominio público que usa el repo). No hace falta ningún asset nuevo.
- Tipografía: Alice + Roboto, ya vendorizadas en `static/fonts/` — usar esos `@font-face`, no Google Fonts (el prototipo usa Google Fonts solo porque no tenía acceso a los binarios).
- Sin iconos ni imágenes nuevas.

## Files
- `SAIMC Home.dc.html` — prototipo interactivo completo (mapa, chips, paneles, hoja de detalle, los dos layouts de convivencia, el estado de dato escaso).
- `province-paths.js` — geometría de provincias usada por el prototipo (copia de `map_cuba.py`; en producción seguir usando `map_cuba.py` directamente).
- `saimc-mock-data.js` — datos de precio sintéticos + generador determinista; reemplazar por las consultas reales de `card_view.py` / `series_view.py`.
