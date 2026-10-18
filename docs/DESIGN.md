# DESIGN — cómo se ve y se decide la interfaz de SAIMC

Léelo antes de tocar una plantilla o una hoja de estilo. No repite los valores
de los tokens: la fuente de verdad es `src/saimc/web/design_tokens.css`. Acá van
las reglas de uso y las decisiones que el CSS no dice.

## Tokens: dónde viven y cómo se cambian

- Fuente única: `src/saimc/web/design_tokens.css` (no se sirve).
- Se edita ahí, se corre `python tools/build_css_tokens.py` y se commitean los
  tres archivos juntos. El bloque se copia dentro de `static/saimc-material.css`
  y `static/saimc-public.css`; `tests/test_static_assets.py` falla si se
  desincronizan.
- La terminal del operador (`saimc.tui`) también sale de ahí: lee los tokens
  `--m3-` del bloque oscuro al arrancar (`saimc/tui/theme.py`) y falla si falta
  uno. Su avatar copia los hex de `_guani_avatar.html`, vigilado por test.
- Nunca se declara un color con valor suelto en una plantilla ni se escribe
  `var(--m3-x, #fallback)` con un fallback inventado: ya pasó, y el fallback
  ganaba siempre con un color de otro esquema.
- Todo color nuevo lleva su versión oscura en `:root[data-theme="dark"]`, con
  contraste WCAG medido (texto ≥ 4,5:1, borde de UI ≥ 3:1). Si no tiene
  versión oscura a propósito (el cielo de la landing), se anota en
  `SIN_VERSION_OSCURA` con el motivo.

## Dos capas, dos marcas

| Capa | Prefijo | Quién la ve | Acento |
|---|---|---|---|
| Herramienta del operador (`/curation`, `/control`) | `--m3-` | operador | teal `--m3-primary` |
| Pública (`/`, producto, mapa, Explorar, metodología) | `--ph-` | visitante | azul `--ph-accent` |

Son dos colores de marca deliberados (decidido el 2026-09-04). No se reemplaza
uno por el otro. Los titulares y las cifras públicas van en serif (`--ph-font-heading`,
Alice); el resto, en Roboto (`--m3-font`) y Roboto Mono para código.

## Reglas de uso

1. **Mobile first.** Se decide a 390 px y después se escala. El objetivo táctil
   mínimo es 44 px. Si una codificación visual no entra en un teléfono, se
   cambia la codificación; no se deja rota en móvil. La conexión del usuario
   final es lenta: nada de SPA, render en servidor, activos servidos desde el
   repo (CLAUDE.md, decisión 1).
2. **Un atajo oculta al formulario largo.** Cuando hay una acción de un clic para
   el caso común y un camino completo para el excepcional, el segundo va en un
   `<details>` cerrado. No se muestran los dos a la misma altura.
3. **Rojo/verde no significa precio.** Los colores de provincia (`--m3-prov-*`),
   los acentos de dato (`--m3-acento-*`) y la escala de precio (`--ph-price-*`)
   quedan fuera del eje error/éxito a propósito. Un precio no está «bien» o
   «mal»; solo se ubica entre lo observado. No hay semáforo.
4. **Nada de texto sobre un degradado secuencial.** El medio no llega a 4,5:1.
   La cifra va sobre la superficie, junto a la barra.
5. **Espaciado en múltiplos de 4:** `--m3-space-N` es N × 4 px. Radios y
   sombras salen de `--m3-radius-*` y `--m3-elev-*`; la capa pública usa
   `--ph-radius-*` y `--ph-elev`.
6. **Ícono = Material Symbols Rounded** (`.m3-icon`, subset vendorizado;
   se regenera con `tools/build_icon_font.py`). Fuentes y activos se sirven
   desde `/static`, nunca desde un CDN.
7. **Animación solo CSS y solo lo barato**, con `prefers-reduced-motion`
   respetado. Sin JavaScript nuevo para decorar. (El tour de primera visita del
   mapa es JS funcional, no decoración: ver `docs/decisions.md`, 2026-10-18.)

## Landing: la página es un día

Cada grupo de secciones tiene su hora: atardecer (portada), noche («Así
trabajo»), amanecer («Por qué puedes fiarte»), día (el resto) y ocaso (antes del
pie). Referencia estética: ilustración propia en CSS y SVG en línea, sin fotos
ni imágenes raster nuevas. Los tokens del cielo son `--ph-dusk-*`.

- Descartado: cielos terracota o crema (el tono por defecto de lo generado),
  hacer toda la página de noche, fotos o ilustraciones raster.
- La landing habla de Guani en tercera persona.
- Detalle y motivos: `docs/decisions.md`, entradas del 2026-10-11 sobre la landing.

## Cómo se verifica un cambio de UI

- Si hay un prototipo aprobado (`docs/prototipos/*.html`, o un `.dc.html` de
  Claude Design), **la vara es el prototipo renderizado, no el plan.** Antes de
  decir «cerrado», renderizá prototipo y página real a 390 y 1280 px y
  comparalos pantalla a pantalla. Los tests y las medidas de altura no prueban
  fidelidad.
- Si se implementa la filosofía del prototipo y no su disposición, se dice
  desde el principio y se pide aprobación de la diferencia.
- Prototipos vigentes de la landing: `landing-dia-completo.html`,
  `landing-estetica.html`, `landing-entradas.html`, `landing-narrativa.html`.
  Las piezas de marca para redes están en `marketing-design/project/`.
