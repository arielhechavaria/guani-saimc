# Handoff: rediseño Material 3 de /catalog, /curation y /control (SAIMC)

## Overview

Rediseño visual de las tres vistas web de SAIMC (`arielhechavaria/guani-saimc`, rama `master`):
`/catalog`, `/curation` y `/control`. No cambia el modelo de datos, ni las rutas, ni la lógica
de negocio: cambia **jerarquía, navegación, tipografía/color y estados/feedback**.

Objetivo declarado por el usuario: herramienta interna para el operador de curaduría, tema
claro, densidad media, estética Material Design. Sin tooling nuevo (sin build, sin npm, sin
CDN — se mantiene el criterio de `CLAUDE.md`: todo vendorizado, una descarga menos en
conexión lenta).

## About the Design Files

Los archivos de este bundle son **referencias de diseño hechas en HTML** — un prototipo que
muestra el aspecto y el comportamiento buscados, **no código de producción para copiar y
pegar**. `SAIMC Rediseño.dc.html` es una maqueta React de una sola página con datos de
ejemplo y las tres vistas conmutadas por navegación interna.

La tarea es **recrear ese diseño en el entorno real del repo**: FastAPI + Jinja2 +
templates server-rendered, sin JavaScript de framework. Es decir:

1. Reemplazar `bootstrap.min.css` + `curation.css` por la hoja `saimc-material.css` que
   viene en este bundle (ya escrita, lista para `src/saimc/web/static/`).
2. Reescribir el markup de los tres templates Jinja usando sus clases.
3. No introducir React, ni un bundler, ni Tailwind, ni un CDN de fuentes en runtime.

`saimc-material.css` **sí es entregable de producción**: está escrita para el repo, con
tokens en `:root` y clases prefijadas `m3-`. Usala como está y ampliala si falta algo.

## Fidelity

**High-fidelity.** Colores, tipografía, espaciado, radios, sombras y estados son finales.
Recréalo fielmente. Cuando el prototipo y este README difieran, gana este README.

## Restricciones del repo (leer antes de tocar nada)

Los tests son extensos y muchos afirman sobre el HTML: `tests/test_catalog.py`,
`tests/test_curation.py`, `tests/test_control.py`, `tests/test_curation_auth.py`.

- **No renombrar ni quitar** atributos `name=`, `id=`, `value=`, `action=`, `method=` de
  formularios y campos. Los nombres indexados de la curaduría (`product_name_0`, `price_0`,
  `row_count`, …) y las acciones (`/catalog/channels/{id}/province`, `/curation/{id}/discard`,
  …) se mantienen tal cual.
- **No cambiar el patrón sin-JavaScript.** El colapso sigue siendo `<details>/<summary>`
  nativo; el auto-submit de provincia/dirección sigue siendo `onchange="this.form.submit()"`;
  las pestañas de provincia siguen siendo enlaces con estado en la URL (`?province=`), no un
  control con script. El único JS que existe hoy (deshabilitar el botón de backfill,
  `removeProductCard`) se conserva.
- Las clases de Bootstrap **sí** se pueden quitar, salvo donde un test las use como selector.
  Antes de borrar una clase, `grep` en `tests/`. Si un test depende de `class="badge"` o
  similar, dejá la clase en el atributo y agregá la clase `m3-*` al lado.
- Correr `.venv/bin/pytest` después de cada template. Si un test falla por presentación y
  no por comportamiento, actualizá el test — pero solo en ese caso, y dejándolo dicho.

## Design Tokens

Todos viven en `:root` de `saimc-material.css`. Los valores exactos:

### Color (Material 3, esquema claro, source teal — deriva del `#0f766e` que ya usaba el proyecto)

| Token | Hex | Uso |
| --- | --- | --- |
| `--m3-primary` | `#00696e` | acciones, enlaces, barras de progreso, iconos activos |
| `--m3-primary-hover` | `#00565b` | hover/pressed del botón relleno |
| `--m3-on-primary` | `#ffffff` | texto sobre primary |
| `--m3-primary-container` | `#cce8e9` | botón tonal, chip seleccionado, banner informativo, pill del rail activo |
| `--m3-on-primary-container` | `#041f21` | texto sobre primary-container |
| `--m3-primary-fixed-dim` | `#b8ecef` | barra de progreso sobre fondo primary |
| `--m3-bg` | `#f2f6f6` | fondo de la app y del rail/topbar |
| `--m3-surface` | `#ffffff` | tarjetas y tablas |
| `--m3-surface-container` | `#eef4f4` | formulario embutido dentro de una tarjeta |
| `--m3-surface-container-high` | `#e3e9ea` | hover de icon-button, chip neutral, pista de progreso |
| `--m3-surface-hover` | `#f7fafa` | hover de fila de tabla, fondo de `<pre>` |
| `--m3-on-surface` | `#191c1c` | texto principal |
| `--m3-on-surface-variant` | `#3f4949` | texto secundario, labels, cabeceras de tabla |
| `--m3-outline` | `#6f7979` | borde de campo, borde de botón outlined |
| `--m3-outline-variant` | `#c4cfcf` | borde de chip, línea base de pestañas |
| `--m3-divider` | `#eef2f2` | separador de filas de tabla |
| `--m3-disabled` | `#8a9494` | canal apagado, producto fuera del foco, ceros |
| `--m3-error` | `#ba1a1a` | descartar, eliminar |
| `--m3-error-container` / `--m3-on-error-container` | `#ffdad6` / `#410002` | banner y hover de error |
| `--m3-success-container` / `--m3-on-success-container` | `#c3ecc9` / `#05210d` | chip «webhook activo», «con dato», «bajo el 30%» |
| `--m3-warn-container` / `--m3-on-warn-container` | `#ffe6a8` / `#3d2f00` | chip «sin dato», «fuera de foco», aviso de compra mínima |
| `--m3-warn` | `#e0a700` | borde superior de tarjeta de hueco, borde de fila «revisar» |

**Paleta de provincia** (categórica, de identidad — nunca semáforo; `spec.md` §12.6 prohíbe
fijar umbrales, así que no hay rojo ni verde). Tres tonos por ranura, igual que hoy:

| Ranura | `--prov` | `--prov-soft` | `--prov-wash` |
| --- | --- | --- | --- |
| `.m3-slot-0` | `#3f5fd6` | `#dbe4ff` | `#f6f8ff` |
| `.m3-slot-1` | `#7048e8` | `#e5dbff` | `#f9f6ff` |
| `.m3-slot-2` | `#0b7285` | `#c5f6fa` | `#f0fbfc` |
| `.m3-slot-9` (sin provincia) | `#6f7979` | `#e3e9ea` | `#f7fafa` |

La ranura la sigue asignando `saimc.web.matrix` por catálogo, no por los datos. No cambia.

### Tipografía

- Cuerpo y UI: **Roboto** 300/400/500/700 → `--m3-font`.
- Cifras, IDs, código, ejes: **Roboto Mono** 400/500 → `--m3-mono`, con
  `font-variant-numeric: tabular-nums`.
- **Vendorizar las fuentes** en `src/saimc/web/static/fonts/` y declararlas con `@font-face`.
  No usar `fonts.googleapis.com` en runtime.

| Rol | Tamaño / línea | Peso | Tracking |
| --- | --- | --- | --- |
| Título de página (`.m3-title`) | 28 / 36 | 400 | 0 |
| Subtítulo de página (`.m3-subtitle`) | 14 / 20 | 400 | 0 |
| Kicker (`.m3-kicker`) | 11 | 500 | 1.2px, mayúsculas |
| Título de sección (`.m3-h2`) | 20 | 500 | .15px |
| Título de tarjeta (`.m3-h3`) | 16 | 500 | 0 |
| Cuerpo | 14 / 20 | 400 | 0 |
| Nota / ayuda (`.m3-note`, `.m3-help`) | 13 / 19 y 12 | 400 | 0 |
| Cabecera de tabla | 12 | 500 | .5px, mayúsculas |
| Etiqueta de métrica | 12 | 500 | .6px, mayúsculas |
| Cifra grande (`.m3-metric-value`) | 40 / 1 | 500 mono | tabular |
| Cifra media (`.m3-metric-value-sm`) | 30 / 1.2 | 500 mono | tabular |
| Botón | 14 / 20 | 500 | .1px |
| Chip / badge | 13 y 11 | 500 | 0 y .4px |

Medida de lectura: `max-width: 62ch` en el subtítulo, `80ch` en las notas, `text-wrap: pretty`.

### Espaciado, radio, sombra

- Escala de espaciado usada: **4 / 6 / 8 / 10 / 12 / 14 / 16 / 20 / 24 / 32** px.
- Contenido: `padding: 0 32px 96px`, `max-width: 1200px`. Topbar `16px 32px`.
- Gap entre secciones: 24px (catálogo/curaduría), 32px (control). Gap de grillas: 16px.
- Radios: campo 4px · chip/snackbar 8px · banner/métrica pequeña 12px · tarjeta 16px ·
  botón y contador 100px (pill).
- Sombras: tarjeta `0 1px 3px rgba(0,0,0,.08)` · botón relleno `0 1px 2px rgba(0,0,0,.2)` ·
  barra de acciones `0 -1px 3px rgba(0,0,0,.06), 0 4px 12px rgba(0,0,0,.18)` ·
  snackbar `0 4px 12px rgba(0,0,0,.28)`.

### Iconos

**Material Symbols Rounded**, peso 400, tamaños 16 / 20 / 24. Vendorizar el `.woff2` en
`static/fonts/` (subset con los glifos usados alcanza) y usarlo por ligadura:
`<span class="m3-icon">inventory_2</span>`.

Ligaduras usadas: `inventory_2` `fact_check` `monitoring` `help` `add` `edit` `delete`
`toggle_on` `toggle_off` `bolt` `edit_note` `place` `open_in_new` `center_focus_strong`
`check` `close` `block` `warning` `check_circle` `visibility_off` `content_paste`
`description` `forum` `schedule` `layers` `auto_awesome` `straighten` `sell`
`currency_exchange` `expand_more` `expand_less`.

Los emoji desaparecen: el `✅` del estado vacío de la cola pasa a `check_circle` en primary.

---

## Screens / Views

Layout común de las tres: **shell de dos columnas** — `.m3-shell` → `.m3-rail` (88px, fijo,
`position: sticky; height: 100vh`) + `.m3-main`. Dentro de `.m3-main`: `.m3-topbar` (sticky,
fondo `--m3-bg`) y `.m3-content`.

Esto reemplaza los tres encabezados distintos que hay hoy (el gradiente teal `.saimc-header`
de curación/control y la fila de enlaces de catálogo). Va a un partial nuevo:
`templates/_shell.html`, con `{% block %}` para título, subtítulo y contenido, más el
destino activo del rail. Los tres templates pasan a extenderlo.

### Rail (navegación)

- Marca: cuadrado 56×56, radio 16, `--m3-primary`, texto `SA` blanco 500/15px, margen inferior 12.
- Tres destinos, en orden: **Catálogo** (`inventory_2`, `/catalog`), **Curaduría**
  (`fact_check`, `/curation`), **Control** (`monitoring`, `/control`).
- Cada destino: pill de 56×32 radio 16 con el icono 24px, y debajo la etiqueta 12px/500,
  tracking .5px. Activo → pill `--m3-primary-container`, texto `#00373a`. Hover del inactivo
  → pill `--m3-surface-container-high`, transición `background .15s`.
- `help` al pie (`margin-top: auto`), icon-button de 40px.
- El activo se marca desde el servidor: `_shell.html` recibe `active="catalog"|"curation"|"control"`.
- Bajo 720px el rail se oculta (`display: none`); si querés navegación en móvil, la variante
  de pestañas (`.m3-tabs` bajo el título) es el reemplazo — está en el prototipo con el
  tweak `navStyle`.

### Topbar (cabecera de página)

Fila `align-items: flex-end`, wrap:

- Izquierda: kicker `SAIMC · Fase 0`, `<h1 class="m3-title">`, `<p class="m3-subtitle">`.
- Derecha: tres `.m3-stat` (tarjeta blanca radio 12, padding 10/16, sombra `0 1px 2px rgba(0,0,0,.06)`)
  con etiqueta 11px mayúsculas y cifra mono 20px/500. **En cola** en `--m3-primary`,
  **Curados** y **Filas de precio** en `--m3-on-surface`. Vienen de los mismos contadores
  que hoy alimentan `.saimc-counters`, así que están disponibles en las tres vistas.

Títulos y subtítulos exactos:

| Vista | `m3-title` | `m3-subtitle` |
| --- | --- | --- |
| /catalog | Catálogo del foco | La ronda de canales y los productos piloto. Lo que se define acá decide qué extrae la curaduría. |
| /curation | Cola de curaduría | Un anuncio por vez: mirá la evidencia, confirmá lo que el motor propuso y curá. |
| /control | Control de curaduría | Cuánto falta para la meta de Fase 0, y si lo que se carga sirve. |

---

### 1. `/catalog` — Catálogo del foco

**Propósito.** El operador define la ronda de canales y qué productos están en el foco.

**Layout.** `.m3-stack` (columna, gap 24) con: banner del foco → tarjeta *Canales* → tarjeta *Productos*.

**a) Banner del foco.** `.m3-banner` (primary-container), icono `center_focus_strong` en
primary, texto: «**{{ pilot_count }} productos en el foco.** La curaduría solo extrae precio
de los productos piloto; los demás conservan su historial pero no reciben precios nuevos.»
Reemplaza el `alert alert-secondary` actual y sube al tope, porque explica las dos tablas.

**b) Tarjeta Canales.** `.m3-card`.

- `.m3-card-head`: `h2` «Canales y grupos seguidos» + nota 13px: «La ronda diaria. Un canal
  con Id. WAHA recibe por webhook; el resto es captura manual. Si le fijás provincia, esa
  gana siempre en la curaduría.» (Los tres párrafos largos de hoy se condensan a esta línea.)
  A la derecha, botón relleno `add` **Agregar canal**.
- **El formulario de alta pasa a colapsado** (`<details>` con el botón como `<summary>`, o el
  patrón que ya usa «Pegar anuncios»). Abierto: bloque `--m3-surface-container` radio 12,
  padding 20, `.m3-grid-fields` (auto-fit minmax(200px,1fr), gap 22/16). Campos outlined con
  label notcheado — usar `.m3-field-on-container` para que el fondo del label iguale el del
  bloque. Orden: Nombre (obligatorio, label en primary) · Plataforma · Provincia · Id. WAHA
  (mono, ayuda «Cargarlo automatiza la captura») · Dirección del local (span 2) · Enlace ·
  Notas. Acciones: relleno **Guardar** + texto **Cancelar**.
- **Tabla** `.m3-table`, columnas: `Canal` · `Provincia fijada` · `Dirección` · `Captura` ·
  `Acciones` (derecha). Se pasa de 7 columnas a 5 fusionando plataforma y notas en la línea
  secundaria del nombre.
  - *Canal*: nombre 500 + `open_in_new` 16px `--m3-outline` si tiene URL; debajo
    `.m3-cell-sub` con `{{ platform }} · {{ notes }}`. Canal apagado → `.m3-row-off`
    (texto `--m3-disabled`).
  - *Provincia fijada*: el `<select>` con `onchange="this.form.submit()"` **se conserva**,
    estilado como campo compacto; cuando el valor está fijado se muestra además el chip
    `place` + nombre. Mínimo 150px de ancho, igual que hoy.
  - *Dirección*: el input con `onchange` submit, placeholder «Sin cargar», mínimo 200px.
  - *Captura*: chip `bolt` «webhook activo» (success-container) o chip `edit_note` «manual»
    (neutral). Si tiene `external_id`, debajo botón outlined pequeño **Importar histórico**
    (conserva el JS que lo deshabilita y le pone «Importando…»).
  - *Acciones*: tres icon-buttons de 40px — `edit` (abre el `<details>` de edición, que se
    mantiene tal cual), `toggle_on`/`toggle_off` en primary/disabled para encender-apagar
    (reemplaza los botones «Apagar»/«Encender», mismo POST), `delete` en error con el
    `confirm()` actual.
  - Hover de fila: `--m3-surface-hover`.

**c) Tarjeta Productos.** `.m3-card`.

- Head: `h2` «Productos» + «{{ products|length }} en catálogo · {{ pilot_count }} en el foco».
  Luego dos chips-filtro (`.m3-chip-btn`): **Todos** / **Solo el foco** (con `check` cuando
  está activo). El filtro es de pantalla: implementarlo como enlaces `?focus=1` para no
  agregar JS. Botón tonal `add` **Agregar al foco**, que abre el formulario de alta colapsado
  (Nombre · Categoría · Unidad + ayuda «Se agrega directamente como piloto: si lo cargás acá,
  lo querés medir»).
- Tabla: `Producto` · `Categoría` · `Observaciones` · `Foco` (derecha).
  - *Producto*: nombre 500 + `.m3-badge` «PILOTO» si aplica; debajo `.m3-cell-sub`
    «se mide por {{ unit }}».
  - *Observaciones*: cifra mono 15px/500 (min-width 38px) + barra `.m3-progress-thin`
    (6px, radio 3, máx 160px) normalizada contra el máximo de la tabla. Fuera del foco →
    barra `#bec8c9`, texto `--m3-disabled`.
  - *Foco*: fuera del foco → botón tonal **Poner en el foco**; dentro → outlined
    **Sacar del foco**. Mismo POST de hoy.

---

### 2. `/curation` — Cola de curaduría

**Propósito.** Curar un elemento por vez: mirar la evidencia, confirmar o corregir lo que
propuso el motor de reglas, y guardar o descartar.

**Cambio estructural.** Hoy la evidencia está arriba y el formulario abajo, así que hay que
scrollear entre lo que se mira y lo que se escribe. Pasa a **dos columnas**
(`.m3-curation`: `minmax(300px,5fr) minmax(360px,7fr)`, gap 20, `align-items: start`), con la
evidencia **sticky** a `top: 180px`. Bajo 900px vuelve a una columna y pierde el sticky.

**a) Barra de contexto** (fila de chips, sobre las columnas):

- Pill blanco: icono `layers` primary + «Elemento **1** de **{{ pending }}** por curar».
- Si `out_of_focus`: pill `--m3-warn-container` con `visibility_off` + «{{ n }} fuera de foco»,
  con el `title` que ya existe («Sin producto del foco: vuelven si su producto entra al foco»).
- A la derecha, botón tonal `content_paste` **Pegar anuncios** (abre el `<details>` de pegado).

Los contadores Curados / Descartados suben a los `.m3-stat` del topbar; no se duplican acá.

**b) Pegar anuncios.** `<details>` → `.m3-card-pad`. Título 18px, nota con el `---` en
`<code>`, `<textarea class="m3-textarea" rows="7">` (mono 14/22, fondo `--m3-surface-hover`,
radio 8, resize vertical), acciones alineadas a la derecha: texto **Cancelar** + relleno
**Meter en la cola**. `action="/curation/paste"` y `name="pasted"` sin cambios.

**c) Columna izquierda — Evidencia.** `.m3-card.m3-evidence`.

- Cabecera con separador inferior: `description` primary + `h3` «Evidencia» + a la derecha
  `texto · #{{ ingestion.id }}` (o `imagen · #…`) en mono 12px.
- Cuerpo: `<pre>` estilado (`.m3-evidence pre`) o `<img>` para captura (radio 8, sombra,
  `max-height` como hoy).
- `.m3-evidence-meta`: tres líneas con icono 18px `--m3-outline` — `forum` canal origen ·
  `schedule` «Capturado {{ hora }} (La Habana)» · `place` «Provincia del canal: **X** —
  manda sobre el texto» (solo si el canal la tiene fijada).

**d) Columna derecha — Datos del anuncio.**

- Tarjeta *Datos del anuncio*: `h3` + chip success `auto_awesome` «{{ n }} candidatos
  propuestos por el motor» (si el motor no encontró nada, chip warn «sin candidatos» y se
  mantiene el atajo de descarte que ya existe, como `.m3-banner-warn` con botón danger).
  Grilla de dos campos: **Fecha de publicación \*** (type=date, `required`, `max` = fecha de
  captura, label en primary, ayuda «La fecha del anuncio, no la de hoy») y **Canal origen**.
- Una `.m3-card.m3-row-card` por candidato (reemplaza `_price_row_card.html`, que se
  reescribe con las clases nuevas y sigue compartido con `curation_edit.html`):
  - Borde izquierdo 4px: `--m3-primary` si el motor lo detectó, `--m3-warn` si hay que
    revisar (`.is-review`).
  - Cabecera: `.m3-row-num` (círculo 28px primary-container con el índice) + `h3` con el
    texto reconocido + chip de estado («detectado por reglas» success / «revisar cantidad»
    warn) + icon-button `close` en error a la derecha (llama al `removeProductCard` actual).
  - Campos `.m3-grid-fields` (minmax(150px,1fr), gap 22/14): **Producto** (select, span 2,
    label primary) · **Precio** (mono 500) · **Moneda** · **Cantidad** (mono 500) · **Unidad**.
  - Fila de formato: etiqueta «FORMATO» 12px mayúsculas + dos `.m3-chip-btn`
    **Minorista** / **Mayorista** (radios nativos estilados como chips: `<input type="radio">`
    visualmente oculto + `<label>`; el `name` indexado se conserva). A la derecha, enlace
    `add` **Compra mínima y provincia** que abre el `<details>` de campos secundarios que ya
    existe.
  - Si la compra mínima supera la cantidad: `.m3-banner-warn` dentro de la tarjeta con
    `warning`, explicando el arreglo (cargar el lote como lote).
- Botón outlined `add` **Agregar otro producto** al pie de la columna.

**e) Barra de acciones.** `.m3-actionbar` sticky abajo (tarjeta blanca radio 16, sombra
doble). Izquierda: nota «Curar guarda las filas en `prices_history` y pasa al siguiente.»
Derecha: botón `.m3-btn-danger` `block` **Descartar** (form `discard-form`) y
`.m3-btn-filled.m3-btn-lg` `check` **Curar anuncio** (form `curation-form`). Los `form=`
apuntando a formularios por id se conservan.

**f) Estado vacío.** `.m3-empty` dentro de una tarjeta: `check_circle` 48px en primary +
«No hay elementos en la cola.»

---

### 3. `/control` — Control de curaduría

**Propósito.** Ver cuánto falta para la meta de Fase 0 y si lo que entra sirve.

**Cambio estructural.** El contenido no cambia; cambia la jerarquía. Hoy hay ~10 párrafos
largos de justificación de spec entre las secciones, y todo compite al mismo volumen. Ahora:
`h2` de sección + **una** línea de contexto de 13px; las justificaciones largas se condensan
en una grilla de cuatro notas cortas antes de la matriz (ver más abajo). El contenido
recortado no se pierde: va a `docs/` si hace falta, no a la pantalla del operador.

Layout: `.m3-stack-lg` (gap 32) con siete secciones en el mismo orden que hoy.

**a) Meta de Fase 0.** `h2` + «500 registros · {{ n }} productos · {{ m }} provincias —
números de `spec.md` §9». Grilla de tres:

- **Métrica destacada** `.m3-metric.is-hero` (fondo `--m3-primary`, texto blanco, sin sombra):
  etiqueta «Registros validados», cifra mono 40px + `/ 500` al 75% de opacidad,
  `.m3-progress` (pista blanca al 28%, relleno `--m3-primary-fixed-dim`),
  y hint «Faltan {{ x }} · {{ pct }}% · a {{ per_day }} filas/día son **{{ days }} días**».
  Es la única superficie de color de la vista: es el número que importa.
- **Productos bandera con dato**: cifra `4 / 5` + chips (success «con dato», warn
  «{{ nombre }} · sin dato»).
- **Provincias objetivo**: igual patrón.

**b) Ritmo de carga.** `h2` + «últimos {{ activity_days }} días · se cuenta cuándo se cargó
la fila, no la fecha del anuncio». Tarjeta con grilla `minmax(150px,1fr) 3fr` alineada al pie:
promedio diario (mono 40px) + «{{ rows }} filas en {{ days }} días» a la izquierda; a la
derecha `.m3-activity` (barras 96px de alto, gap 6, radio 4 arriba, primary; días sin carga en
`--m3-surface-container-high`) con eje `.m3-activity-axis` en mono 11px. `title` por barra
«{{ día }}/{{ mes }} — {{ n }} filas». Normalización contra el pico, igual que hoy.

**c) Lo que falta responder.** `h2` + nota: «Ningún campo bloquea el guardado: la fila entra
igual y el operador sigue curando. Por eso hay que verlos acumulados. Cada número abre el
historial ya filtrado.» Grilla de `.m3-gap` (borde superior 3px `--m3-warn`, o
`.is-clear` verde `#1f6b3a` cuando el conteo es 0): icono + etiqueta mayúsculas, cifra mono
30px enlazada a `/curation/history?{{ gap.query }}`, y hint 12px. Iconos por hueco:
`place` / `straighten` / `sell` / `currency_exchange`.

**d) Cobertura de la meta.** `h2` + «producto × provincia» + nota: «**El cero es el dato**:
dice qué falta salir a capturar. No hay marca de "suficiente" — el mínimo por celda es justo
el parámetro que §12.6 manda calibrar con datos reales.» Tabla en tarjeta, una columna por
provincia + **columna Total** (nueva), cifras mono 15px alineadas a la derecha, enlazadas al
historial filtrado; el cero se dibuja `—` en `--m3-disabled`.

**e) Salud de la ingesta.** `h2` + «la tasa de descarte se mide sobre lo decidido». Cuatro
`.m3-metric` con cifra 30px: En cola (primary, enlazada a `/curation`) · Curados ·
Descartados · Tasa de descarte, esta última con chip al lado — success `check_circle`
«bajo el 30%» o warn `warning` «sobre el 30%» — y hint «Objetivo de §12.5 — el único umbral
con veredicto en esta vista». Si está por encima, se mantiene el `.m3-banner-warn` con el
texto largo que ya existe.

**f) Tasa de cambio.** `h2` + nota: «Todo sale de la base: **cargar esta página no consulta a
elTOQUE**. Quien sale a la red es la curaduría. Se muestra la antigüedad en días y nada más —
no hay ventana de validez fijada (§12.6).» Una tarjeta por moneda: par (13px/500 tracking .5)
+ chip de frescura arriba a la derecha («tasa de hoy» success / «de hace N días» warn /
«nunca cacheada» neutral), cifra mono 32px, y meta 12px con fecha, fuente y hora de lectura.
Debajo, la tabla «Qué tasa quedó aplicada en lo cargado» sin cambios de contenido, con
`.m3-table`.

**g) Matriz de los pilotos.** `h2` + «por unidad y formato · últimos {{ days }} días».

- **Las cinco explicaciones largas se condensan** en un bloque `--m3-surface-container` radio
  12, grilla de 4 notas de 13px (auto-fit minmax(240px,1fr)), con la primera frase en negrita:
  1. «**El color dice qué provincia**, no qué tan bien va: sin umbral de dispersión fijado
     (§12.6), la paleta es de identidad y no hay rojo ni verde.»
  2. «**La barra** va del mínimo al máximo; la marca es la mediana. El largo codifica la
     amplitud en escala logarítmica.»
  3. «**El precio es por unidad**: cada fila se divide por su cantidad. Una fila sin cantidad
     queda aparte, marcada "precio total".»
  4. «**Una tarjeta vacía es un hueco de cobertura** y no se esconde: cae al final con su cero.»
- Pestañas de provincia: `.m3-tabs` con `.m3-tab` + `.m3-tab-count`. Siguen siendo enlaces
  `?days=…&province=…`, con `aria-current="page"` en la activa, y llevan la clase de ranura
  (`m3-slot-N`) para que el color de la activa sea el de su provincia.
- Una `<details class="m3-card m3-matrix-card">` por producto. `summary`: chevron
  `expand_more`/`expand_less` (`[open]` alterna vía CSS o dos spans), nombre en 15px/500,
  «{{ total }} observaciones», y a la derecha el reparto por provincia (`.m3-matrix-tally`
  con `.m3-matrix-dot` del color de su ranura; la provincia que no se está mirando al 50% de
  opacidad). Abierta solo la de mayor muestra, igual que hoy.
- Cuerpo `.m3-matrix-body` (fondo `--prov-wash`, borde izquierdo 4px `--prov`): una
  `.m3-serie` por serie con «unidad · formato» 14px/500, «n = N · mediana **X** CUP/unidad ·
  amplitud N×» 13px, y la barra `.m3-range` — pista blanca 10px radio 5, relleno
  `--prov-soft` al ancho logarítmico, marcador de mediana 3×16px en `--prov`. Mín y máx en
  mono 12px a los costados. Sin serie → «— sin series cargadas —» y la tarjeta cae al final.

---

## Interactions & Behavior

Todo server-rendered; el estado vive en la URL o en el POST. Nada de esto necesita JS nuevo.

- **Navegación**: rail y pestañas son `<a href>`. El destino activo lo marca el servidor.
- **Colapsos** (alta de canal, edición de canal, pegado, campos secundarios, tarjetas de la
  matriz): `<details>/<summary>` nativo, sin script. Quitar el marcador por defecto
  (`list-style: none` + `::-webkit-details-marker { display: none }`).
- **Auto-submit** de provincia y dirección en la tabla de canales: `onchange="this.form.submit()"`,
  igual que hoy.
- **Backfill**: se conserva el script que deshabilita el botón y le pone «Importando…».
- **Filtro de productos** (Todos / Solo el foco) y **pestaña de provincia**: enlaces con
  querystring, no JS.
- **Snackbar** (`.m3-snackbar`): es el reemplazo de los `alert` de flash. Se renderiza cuando
  hay `flash_message` / `flash_error` / `pasted` en el contexto, aparece con
  `m3-snack-up .2s ease-out`, y se cierra con un enlace de 8px de padding. Sin JS se queda
  visible hasta la próxima navegación; si querés autocierre, son cuatro líneas de
  `setTimeout` inline — opcional, no es requisito.
- **Hover**: fila de tabla → `--m3-surface-hover`; icon-button → círculo
  `--m3-surface-container-high` (error → `--m3-error-container`); botón relleno → `--m3-primary-hover`;
  outlined/texto → tinte `#e2eeee` / `#dbe9e9`. Transición `.15s ease` en `background`.
- **Focus**: `:focus-visible { outline: 2px solid var(--m3-primary); outline-offset: 2px }`.
  Campo enfocado: borde 2px primary compensando 1px de padding para que no salte el layout.
- **Deshabilitado**: opacidad .45.
- **Validación**: sin cambios. `published_date` sigue `required` con `max` = fecha de captura;
  la validación exacta (calendario de La Habana) sigue en `_validated_published_date`.
- **Responsive**: <900px la curaduría cae a una columna y la evidencia deja de ser sticky;
  <720px se oculta el rail y el padding baja a 16px. Las tablas anchas van dentro de
  `.m3-table-scroll`.
- **Estados de carga**: solo el botón de backfill. No hay otros pedidos asíncronos.

## State Management

No hay estado de cliente. Lo que en el prototipo es `useState` corresponde a:

| En el prototipo | En el repo |
| --- | --- |
| página activa | la ruta (`/catalog`, `/curation`, `/control`) |
| formulario de canal abierto | `<details>` nativo |
| pegado abierto | `<details>` nativo |
| filtro de productos | querystring `?focus=1` |
| provincia de la matriz | querystring `?province=` (ya existe) |
| tarjeta de la matriz abierta | atributo `open` en `<details>`, decidido en el template |
| toggles de canal / piloto | POST + redirect (ya existe) |
| snackbar | `flash_message` / `flash_error` en el contexto (ya existe) |

Sin nuevos datos ni endpoints. El único agregado de contexto es el `active` del shell y, si
se implementa el filtro de productos, leer `?focus`.

## Assets

- **Roboto** y **Roboto Mono** (Apache 2.0) → `static/fonts/`, declaradas con `@font-face`.
- **Material Symbols Rounded** (Apache 2.0) → `static/fonts/material-symbols-rounded.woff2`.
  Conviene subsetear a las ~30 ligaduras listadas arriba.
- Sin imágenes ni SVG propios. La única imagen de la UI es la evidencia capturada, que ya
  sirve `/evidence/{id}`.
- `bootstrap.min.css` (232 KB) se **elimina** al terminar; `saimc-material.css` pesa ~20 KB.
  Verificá antes que ningún template restante (`curation_edit.html`, `curation_history.html`)
  siga dependiendo de él — si sí, migralos en el mismo pase.

## Files

En este bundle:

- `README.md` — este documento. Es autosuficiente.
- `saimc-material.css` — la hoja de estilo lista para `src/saimc/web/static/saimc-material.css`.
- `SAIMC Rediseño.dc.html` — el prototipo de referencia (React de un solo archivo, datos de
  ejemplo). Abrilo en el navegador para ver el resultado buscado y los estados interactivos.
  No es código a portar.

En el repo, a modificar:

| Archivo | Qué hacer |
| --- | --- |
| `src/saimc/web/static/saimc-material.css` | nuevo — copiar de este bundle |
| `src/saimc/web/static/fonts/` | nuevo — vendorizar Roboto, Roboto Mono, Material Symbols |
| `src/saimc/web/templates/_shell.html` | nuevo — rail + topbar + bloques |
| `src/saimc/web/templates/catalog.html` | reescribir markup; extiende el shell |
| `src/saimc/web/templates/curation.html` | reescribir markup; dos columnas |
| `src/saimc/web/templates/_price_row_card.html` | reescribir como `.m3-row-card` |
| `src/saimc/web/templates/control.html` | reescribir markup; sacar el `<style>` embebido |
| `src/saimc/web/templates/curation_edit.html` | migrar clases (usa el mismo partial) |
| `src/saimc/web/templates/curation_history.html` | migrar clases |
| `src/saimc/web/static/bootstrap.min.css` | eliminar al final |
| `src/saimc/web/static/curation.css` | eliminar al final (sus tokens quedan en la hoja nueva) |
| `tests/` | solo si un test afirma sobre presentación; nunca sobre `name`/`action` |

Orden sugerido: hoja + fuentes → `_shell.html` → `control.html` (la más grande, y la que
más gana) → `catalog.html` → `curation.html` + `_price_row_card.html` → los dos templates
restantes → borrar Bootstrap → `pytest`.
