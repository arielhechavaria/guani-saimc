# Plan de trabajo — la fábrica de publicaciones de `/marketing`

Fecha: 2026-10-18. Aceptado por el operador; el descarte de la API de Meta
está en `docs/decisions.md` (2026-10-18). Ninguna tarea está empezada. Reemplaza a T8b de
`docs/plan-bloque-mvp.md` (publicación automática por la API de Meta).

## Por qué este plan

- **La API de Meta no está disponible desde Cuba.** El 2026-10-18 el operador
  intentó registrarse en Meta for Developers y le respondió que no está
  disponible para su país, también con VPN. Sin app de Meta no hay token, así
  que quedan inviables la lectura de métricas (`saimc.instagram`, slice del
  2026-10-13, nunca activado en el VPS: tablas `instagram_*` vacías y
  temporizador sin instalar) y la publicación automática (T8b, decisión del
  2026-10-16).
- **Pedirle la app a un tercero fuera de Cuba se descartó.** Los términos de
  la plataforma de Meta (§6.a.iv) prohíben transferir o compartir tokens de
  acceso y secretos de app, y el arreglo existe solo para esquivar el bloqueo
  por país: arriesga la cuenta de Instagram de Guani a cambio de unos 30
  segundos por publicación.
- **La fábrica no necesita a Meta.** El sistema arma la publicación con datos
  reales y el operador la publica desde el teléfono con el menú «Compartir»
  del sistema operativo.
- `docs/spec.md` no trata marketing ni redes (no figura en `docs/spec-map.md`):
  las decisiones de esta fábrica viven en `docs/decisions.md`.

## Reglas que no se negocian

1. **Las cifras no se editan.** Se elige qué datos van (producto, provincia,
   fecha de corte, qué bloques se ven); nunca se escribe un número. Salen del
   mismo cálculo que el tablero público (`saimc/marketing/cards.py`) y solo de
   filas que cuentan como validadas.
2. **Lo aprobado se congela.** Al aprobar se guarda una copia de los datos
   (`snapshot`) y la imagen final. Si después se corrige un precio, lo
   publicado no cambia y se puede auditar de dónde salió cada cifra.
3. **Nada se publica solo.** Publica siempre el operador desde su teléfono:
   no hace falta interruptor de emergencia ni control de doble publicación.
4. **Mobile first.** La publicación ocurre en el teléfono: toda pantalla se
   decide a 390 px primero (`docs/DESIGN.md`).
5. **HTMX y render en servidor** (decisión cerrada 1). Sin arrastrar y soltar:
   el orden se cambia con botones ↑ ↓. JavaScript propio y mínimo, sin
   librerías nuevas.
6. **Privacidad.** Ninguna pieza muestra teléfonos, nombres, grupos ni canales.
   «De dónde sale este precio» muestra solo fecha y precio de cada anuncio, y
   así sigue.

## El ciclo de una publicación

```
borrador ──aprobar──▶ aprobada ──marcar publicada (+ enlace)──▶ publicada
    │                    │
    └──── descartar ◀────┘   (descartar una aprobada borra su imagen)
```

- **Borrador:** lo crea el lote de las 06:00 o el operador. Se edita.
- **Aprobada:** datos congelados y JPEG hecho. No se edita: si hay que cambiar
  algo se descarta y se duplica.
- **Publicada:** el operador pegó el enlace del post.
- **Fecha prevista** (`planned_for`): cada publicación tiene una. Programar de
  verdad, si hace falta, se hace con la programación propia de Instagram.

## Pantallas

| Pantalla | Qué muestra |
|---|---|
| **Hoy** (`/marketing`) | Lo que toca hoy según el calendario, con miniatura, estado y la acción principal; aviso si falta algo («miércoles sin carrusel»). Abajo, plegadas, las **piezas fijas** (portada de Facebook, story de precalentamiento, «De los grupos a la cifra», vista previa al compartir), que se descargan como hoy. |
| **Editor** (`/marketing/post/<id>`) | Datos (producto, provincia, fecha de corte), bloques con interruptor, textos editables con tope de caracteres, texto del post generado y editable, vista previa en vivo. En carrusel, lista de diapositivas con ↑ ↓ y «+ agregar». |
| **Publicar** (en el teléfono) | **Copiar texto** → **Compartir** (menú del sistema con la imagen o las imágenes; sirve también para WhatsApp y Telegram) → **Marcar publicada** con el enlace. |
| **Semana / historial** | Lunes a domingo con lo que toca y su estado; historial de publicadas con enlace y datos congelados. |

## Tareas

Cada tarea es un slice: su propio worktree, un commit, pasos de verificación
al cerrar (`CLAUDE.md`). La complejidad es **S** (≤ medio día), **M** (1–2
días) o **L** (más de 2 días o con aprobación intermedia). El modelo sigue la
regla del operador: opus para esquema de datos y decisiones de arquitectura,
sonnet para endpoints, plantillas y tests.

| # | Tarea | Esquema | Cmplj. | Modelo | Depende de |
|---|---|---|---|---|---|
| F1 | Editor sin estado: bloques, JPEG, texto del post, Copiar y Compartir | no | M | sonnet | — |
| F2 | Publicaciones con estado: tablas, aprobar, Hoy, marcar publicada, lote crea borradores | **sí** | L | opus | F1 |
| F3 | Carruseles: varias diapositivas, orden, compartir varias imágenes | no | M | sonnet | F2 |
| F4 | Semana e historial | no | S | sonnet | F2 |
| F5 | *(opcional)* Métricas cargadas a mano | **sí** | S | opus | F2 |

**F1 sirve sola**: desde el primer día se arma, se comparte y se publica desde
el teléfono. Si después de una semana de uso F2 resulta innecesaria, no se hace.

### Prefijo común de los prompts

Pegar al inicio de cada prompt (es el mismo de `docs/plan-bloque-mvp.md` §2):

> Proyecto SAIMC/Guani. Lee `CLAUDE.md`. Respondé en español; código en
> inglés. Antes de proponer algo de arquitectura, esquema o métricas, abrí
> `docs/spec-map.md` y leé el rango citado; citá por sección, no por línea.
> Trabajá en un worktree propio (`git worktree add .claude/worktrees/<slice>
> -b worktree-<slice>`); nunca en `master`. Un solo slice. Commits
> convencionales, sin atribución de IA. No hagas push. Si hay cambios sin
> commitear que no continúan esto, parás y avisás. Al cerrar: tests en verde,
> `docs/checklist-pr.md`, entrada en `docs/decisions.md` si la decisión no es
> obvia, y pasos concretos para verificar (URL, qué mirar, qué debe y qué no
> debe pasar). Leé `docs/plan-fabrica-marketing.md` (reglas, ciclo y pantallas)
> antes de empezar.

---

### F1 — Editor sin estado · M · sonnet

**Estado:** `/marketing` elige tarjeta, producto y provincia por formulario
GET, muestra la vista previa en un `<iframe>` y baja un PNG
(`/marketing/card/{slug}.png`). Debajo está la sección «Instagram», vacía
porque el seguimiento nunca se activó.

**Alcance:**
1. **Bloques** en las tres tarjetas con datos (`dato-del-dia`,
   `como-se-movio`, `de-donde-sale`). Cada `CardType` declara sus bloques
   (slug, etiqueta, valor por defecto, si tiene interruptor). Los bloques que
   hacen que la pieza diga algo (título, rango, fecha) no tienen interruptor.
   Las plantillas envuelven cada bloque y el lienzo se reacomoda: apagar un
   bloque no deja un hueco.
2. **Textos editables** (subtítulo y llamada a la acción) con tope de
   caracteres. Ningún parámetro de la URL puede cambiar una cifra.
3. **Configuración en la URL** (bloques y textos como parámetros GET): sin
   tabla ni estado. La vista previa se refresca con HTMX
   (`static/htmx.min.js` ya está vendorizado).
4. **Texto del post** generado desde una plantilla con los datos del contexto
   más hashtags fijos, editable en un `<textarea>` con contador (2200
   caracteres, 30 hashtags).
5. **JPEG** (sRGB) además del PNG: pesa varias veces menos, y el operador lo
   baja al teléfono por la conexión cubana.
6. **Copiar texto** (`navigator.clipboard`) y **Compartir**
   (`navigator.share` con `files`, previa comprobación con `canShare`; si el
   navegador no puede, cae a descargar el JPEG).
7. **Sacar la sección «Instagram»** de la página (plantilla y llamada a
   `load_panel` en el router). `saimc.instagram` y sus tablas quedan
   dormidos, sin borrar; `saimc-instagram.timer` no se instala.

**Fuera de alcance:** guardar publicaciones o estados (F2), carruseles (F3),
las piezas fijas (no cambian).

**Verificación:** en el teléfono, `/marketing?kind=dato-del-dia` → apagar
«comparación entre provincias» → la vista previa se reacomoda sin hueco →
**Copiar texto** y pegarlo en una nota → **Compartir** abre el menú del
sistema con el JPEG → elegir Instagram y ver la imagen en el editor de
publicación (sin publicar). Probar `?b_...=0` sobre un bloque sin interruptor:
la tarjeta lo ignora. La sección «Instagram» ya no aparece.

> **Prompt.** [prefijo] Slice `fabrica-editor`. Leé `docs/DESIGN.md`, las
> entradas de `docs/decisions.md` del 2026-10-14 (estilo de las tarjetas) y
> del 2026-10-18 (se descarta la API de Meta),
> `src/saimc/marketing/{context,cards}.py`, `web/routers/marketing.py`,
> `templates/marketing.html`, `_marketing_card.html` y las plantillas
> `marketing_dato_del_dia.html`, `marketing_como_se_movio.html` y
> `marketing_de_donde_sale.html`. (1) Agregá a `CardType` una tupla de
> bloques (slug, etiqueta, por defecto, con o sin interruptor) para esas tres
> tarjetas. Título, rango y fecha no tienen interruptor. Envolvé cada bloque
> en su plantilla de modo que el lienzo de 1080×1350 se reacomode sin huecos.
> (2) Leé de la URL qué bloques se apagan y dos textos sobrescribibles
> (subtítulo y llamada a la acción) con tope de caracteres. Ningún parámetro
> puede tocar una cifra, y un bloque sin interruptor no se apaga aunque lo
> pidan. (3) La vista previa se refresca con HTMX al cambiar el formulario.
> (4) Creá `saimc/marketing/caption.py` con `build_caption(kind, context)`:
> texto desde una plantilla con los datos, hashtags fijos en una constante,
> 2200 caracteres y 30 hashtags como máximo. Escribí a mano el texto esperado
> antes del código. (5) Agregá la salida JPEG sRGB en `render.py` (Playwright
> `screenshot(type="jpeg", quality=...)`) y una ruta `.jpg`. (6) Botones
> «Copiar texto» (`navigator.clipboard`) y «Compartir» (`navigator.share` con
> `files` si `canShare` lo permite; si no, descarga), en un script propio y
> chico, sin librerías. (7) Quitá la sección «Instagram» de `/marketing` y la
> llamada a `load_panel`. No borres `saimc.instagram`, sus tablas ni sus
> tests: el descarte de la API de Meta ya está en `docs/decisions.md`
> (2026-10-18). Decidí a 390 px primero y verificá contra la tarjeta actual
> renderizada lado a lado. Tests primero para bloques, parámetros, texto del
> post y la ruta JPEG.

---

### F2 — Publicaciones con estado · L · opus

**Estado:** F1 arma y comparte, pero no recuerda nada. El lote
`saimc.marketing.daily` guarda PNG sueltos en `var/marketing/AAAA-MM-DD/`.

**Alcance (toca el esquema: flujo completo, explorar → proponer → especificar
→ diseñar → implementar → verificar):**
1. **Tablas** (propuesta; la confirma la especificación):
   - `marketing_posts`: `id`, `format` (`single`, `carousel`, `story`),
     `status` (`draft`, `approved`, `published`, `discarded`), `planned_for`,
     `caption`, `origin` (`batch`, `operator`), `published_url`,
     `created_at`, `approved_at`, `published_at`.
   - `marketing_post_slides`: `id`, `post_id`, `position`, `card_slug`,
     `params` (JSON: producto, provincia, fecha de corte), `options` (JSON:
     bloques y textos de F1), `snapshot` (JSON, se llena al aprobar),
     `image_path`.
2. **Aprobar:** congela el contexto en `snapshot`, rasteriza el JPEG desde el
   snapshot (no desde la base) y pasa a `approved`. Después ya no se edita.
3. **Pantalla Hoy** en `/marketing` y editor en `/marketing/post/<id>` (el
   editor de F1, ahora guardando `options` y `caption`).
4. **Marcar publicada** con el enlace del post; **descartar** borra la imagen.
5. **El lote de las 06:00 crea borradores** del día con el mismo calendario
   (`WEEKLY_CARDS`) y la misma rotación, a lo sumo uno por día y tipo aunque
   corra dos veces. Deja de escribir PNG sueltos.
6. **Retención:** las publicadas se guardan siempre; borradores y descartadas
   de más de 30 días se borran con su imagen (disco al 85 %).

**Fuera de alcance:** carruseles (F3, aunque la tabla ya los admite),
calendario e historial (F4), métricas (F5).

**Verificación:** correr el lote con `--execute --day <hoy>` dos veces → un
solo borrador de «Dato del día» en Hoy. Editarlo, aprobarlo → cambiar a mano
un precio de la celda en una base de desarrollo → la publicación aprobada
sigue mostrando la cifra vieja. Compartir y marcar publicada con un enlace →
pasa a «publicada». Descartar otra → su JPEG ya no está en disco.

> **Prompt.** [prefijo] Slice `fabrica-publicaciones`. Toca el esquema: seguí
> el flujo completo (explorar → proponer → especificar → diseñar →
> implementar → verificar) y pará a pedir aprobación después de la
> propuesta. Leé `docs/plan-fabrica-marketing.md` (reglas 1–3, ciclo y
> pantallas), `src/saimc/marketing/{context,daily}.py`, el router y el editor
> de F1. Las publicaciones pasan a ser una entidad con estado
> (borrador → aprobada → publicada, o descartada) y diapositivas ordenadas;
> la propuesta de tablas está en F2 y la confirmás o corregís en la
> especificación. Al aprobar se congela el contexto en `snapshot` y el JPEG se
> rasteriza desde él, nunca desde la base viva. `/marketing` pasa a ser
> «Hoy» y `/marketing/post/<id>` el editor que guarda opciones y texto. El
> lote de las 06:00 deja de escribir PNG y crea los borradores del día, de
> forma idempotente por día y tipo. Retención: publicadas para siempre;
> borradores y descartadas de más de 30 días se borran con su imagen.
> Migración con Alembic. Tests primero: transiciones de estado (incluidas las
> prohibidas), snapshot inmutable, idempotencia del lote y retención.
> Actualizá `docs/comandos.md` si cambia algún comando del lote.

---

### F3 — Carruseles · M · sonnet

**Estado:** F2 guarda publicaciones de una diapositiva. La guía de
crecimiento (`docs/guia-crecimiento.md`, «La semana tipo») pide un carrusel
los miércoles (portada + cómo leer + datos + cierre), que hoy se arma a mano y
no está en `WEEKLY_CARDS`.

**Alcance:**
1. Editor de carrusel: lista de diapositivas con ↑ ↓, «+ agregar diapositiva»
   (elige plantilla), quitar, y la vista previa de la diapositiva elegida.
   Tope de 10 diapositivas, mismo ancho y alto en todas.
2. Aprobar congela y rasteriza cada diapositiva.
3. **Compartir** manda todas las imágenes en un solo `navigator.share`. Si el
   teléfono no lo admite para Instagram, **Guardar todas** las baja para
   armar el carrusel desde la app.
4. El lote crea el borrador del carrusel del miércoles con las cuatro
   diapositivas de la guía.

**Fuera de alcance:** reordenar arrastrando; mezclar tamaños.

**Verificación:** el miércoles (o `--day` con un miércoles) aparece un
borrador de carrusel con 4 diapositivas → mover la tercera arriba con ↑ → la
vista previa respeta el orden → aprobar → en el teléfono, **Compartir**
ofrece Instagram con las cuatro imágenes en ese orden (anotar en el cierre
qué pasó en Android y, si se puede, en iPhone).

> **Prompt.** [prefijo] Slice `fabrica-carruseles`. Leé
> `docs/plan-fabrica-marketing.md`, el modelo y el editor de F2 y la «semana
> tipo» de `docs/guia-crecimiento.md`. Hacé que el editor maneje varias
> diapositivas: agregar (eligiendo plantilla), quitar, subir y bajar con
> botones (sin arrastrar), tope de 10, todas del mismo tamaño. Aprobar
> congela y rasteriza cada una. «Compartir» manda todas en un solo
> `navigator.share`; si `canShare` no lo admite, «Guardar todas». Sumá a
> `WEEKLY_CARDS` el carrusel del miércoles (portada, cómo leer, datos, cierre)
> como un solo borrador. Sin cambios de esquema: si hacen falta, pará y
> avisá. Tests primero para el orden, el tope y el borrador del miércoles.
> Probá Compartir en un teléfono real y dejá el resultado en el cierre.

---

### F4 — Semana e historial · S · sonnet

**Estado:** F2 muestra solo el día de hoy.

**Alcance:**
1. Vista de la semana (lunes a domingo): por día, lo que pide el calendario
   (feed y stories, según la guía) y el estado de lo que hay; marca lo que
   falta.
2. Historial de publicadas, de la más reciente a la más vieja, con enlace al
   post y los datos congelados a la vista.
3. Navegar a la semana anterior y a la siguiente.

**Fuera de alcance:** métricas (F5); editar publicadas.

**Verificación:** `/marketing/semana` a 390 px: el miércoles sin carrusel
aparece marcado como faltante; una publicada aparece en el historial con su
enlace, que abre el post de Instagram.

> **Prompt.** [prefijo] Slice `fabrica-semana`. Leé
> `docs/plan-fabrica-marketing.md`, `docs/DESIGN.md`, el modelo de F2 y la
> «semana tipo» de `docs/guia-crecimiento.md`. Agregá `/marketing/semana`
> (con `?desde=AAAA-MM-DD` para moverse por semanas): por día, lo que pide
> el calendario y el estado de lo que existe, marcando lo que falta; y un
> historial de publicadas con su enlace y sus datos congelados. Solo
> lectura, sin cambios de esquema. Decidí a 390 px primero. Tests para la
> semana con días completos, incompletos y vacíos.

---

### F5 — Métricas cargadas a mano (opcional) · S · opus

**Estado:** las métricas solo se ven en Estadísticas de Instagram. La guía de
crecimiento pide una planilla semanal (`docs/guia-crecimiento.md`, «Planilla
semanal»).

**Alcance (toca el esquema: flujo completo):**
1. En una publicada, un formulario corto para copiar a mano de Estadísticas:
   alcance, me gusta, comentarios, guardados, compartidos, y la fecha en que
   se leyó. Se pueden cargar varias lecturas, porque Instagram las recalcula.
2. La planilla semanal sale sola en la vista de F4.

**Fuera de alcance:** leerlas por API (descartado en F1).

**Verificación:** cargar dos lecturas en una publicación → la semana muestra
la última; las dos quedan en el historial de esa publicación.

> **Prompt.** [prefijo] Slice `fabrica-metricas`. Toca el esquema: flujo
> completo, con aprobación después de la propuesta. Leé
> `docs/plan-fabrica-marketing.md`, el modelo de F2, la vista de F4 y la
> «Planilla semanal» de `docs/guia-crecimiento.md`. Permití cargar a mano,
> por publicación, lecturas de alcance, me gusta, comentarios, guardados y
> compartidos con su fecha de lectura (serie de solo altas: Instagram
> recalcula). La vista semanal muestra la última lectura y arma la planilla
> de la guía. Nada de datos de terceros: solo cifras agregadas de la cuenta
> propia. Tests primero.

## Fuera de este plan

- Reels y video.
- Texto del post con IA: una plantilla con datos alcanza y no inventa nada.
- Facebook como canal propio (el botón Compartir ya permite mandar la imagen
  a donde sea).
- Retirar `saimc.instagram`: queda dormido. Borrarlo pide una migración de
  baja y no hace falta ahora.
