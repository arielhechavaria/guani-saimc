# Slice `fabrica-publicaciones` — publicaciones con estado (F2)

Fecha: 2026-10-18. Tarea F2 de `docs/plan-fabrica-marketing.md`; propuesta
aprobada por el operador el mismo día. `docs/spec.md` no trata marketing (no
figura en `docs/spec-map.md`): las decisiones de este slice van a
`docs/decisions.md`.

## 1. Especificación

### 1.1 Entidades

**`marketing_posts`** — una publicación.

| Columna | Tipo | Regla |
|---|---|---|
| `id` | uuid | PK |
| `format` | `single` · `carousel` · `story` | CHECK. F2 crea `single` (1080×1350) y `story` (alto 1920); `carousel` queda para F3 |
| `status` | `draft` · `approved` · `published` · `discarded` | CHECK |
| `planned_for` | date | obligatoria; indexada |
| `caption` | text, nulo | nulo = «automático»: se genera desde los datos hasta que el operador lo edita; al aprobar se congela el que corresponda |
| `origin` | `batch` · `operator` | CHECK |
| `batch_slot` | text, nulo | el slug del calendario (`dato-del-dia`…) si lo creó el lote; nulo si lo creó el operador |
| `published_url` | text, nulo | http(s) |
| `created_at`, `approved_at`, `published_at`, `discarded_at` | timestamptz | |

Restricciones:
- `UNIQUE (planned_for, batch_slot)` — un borrador del lote por día y tipo.
  Como NULL ≠ NULL, las del operador no chocan entre sí. Una descartada sigue
  ocupando el lugar hasta que la retención la borra.
- `(origin = 'batch') = (batch_slot IS NOT NULL)`.
- `approved`/`published` ⇒ `approved_at` no nulo; `published` ⇒
  `published_url` y `published_at` no nulos; `discarded` ⇒ `discarded_at`
  no nulo.

**`marketing_post_slides`** — sus diapositivas, ordenadas.

| Columna | Tipo | Regla |
|---|---|---|
| `id` | uuid | PK |
| `post_id` | uuid | FK `ON DELETE CASCADE` |
| `position` | int ≥ 1 | `UNIQUE (post_id, position)` |
| `card_slug` | text | un slug de `CARD_TYPES` |
| `params` | JSON | `{"product", "province", "data_through"}`: lo que elige el operador; nunca una cifra |
| `options` | JSON | `{"show": {bloque: bool}, "subtitle", "cta"}` (F1) |
| `snapshot` | JSON, nulo | se llena al aprobar y no cambia más |
| `image_path` | text, nulo | relativa a `marketing_dir/posts`; nula si no hay imagen |

**Snapshot** (versión 1):
`{"version": 1, "card_slug", "data_through", "context", "options", "caption"}`.
`context` es el resultado de `build_context` serializado (las dataclasses de
`cards.py` con su tipo), y se reconstruye a las mismas dataclasses para pintar
y para el texto del post. Con el snapshot alcanza para volver a dibujar la
pieza sin leer la base.

### 1.2 Transiciones

| Desde \ a | approved | published | discarded |
|---|---|---|---|
| draft | ✅ aprobar | ❌ | ✅ descartar |
| approved | — | ✅ con enlace http(s) | ✅ borra el JPEG |
| published | ❌ | — | ❌ |
| discarded | ❌ | ❌ | — |

- Toda transición ❌ lanza `InvalidTransition`, y la ruta web responde 409.
- **Editar** (parámetros, opciones, texto) solo se puede en un `draft`.
- **Duplicar** (desde `approved`, `published` o `discarded`) crea un `draft`
  nuevo de `origin = operator` para hoy, con los mismos parámetros y opciones.
  El texto se copia solo si se había editado a mano; si no, queda automático.
- **Aprobar** es todo o nada:
  1. para cada diapositiva arma el contexto con `build_context(…, hoy=data_through)`;
     si no hay datos responde «sin datos» (422) y no cambia nada;
  2. congela el snapshot;
  3. rasteriza el JPEG **desde el snapshot reconstruido**, no desde el
     contexto en vivo;
  4. recién entonces escribe los archivos y cambia el estado.
  Sin Chromium no cambia nada y la ruta responde 503 con el mensaje de
  instalación.
- Inmutabilidad: una vez puesto el snapshot, `snapshot`, `params`, `options`,
  `card_slug` y `position` de esa diapositiva no se pueden cambiar. Un
  listener `before_update` del ORM lanza `FrozenSlideError`. El `caption` de
  una publicación `approved` o `published` tampoco.

### 1.3 Lote de las 06:00 (`saimc.marketing.daily`)

- Mismo calendario (`cards_for_day`) y misma rotación (`candidate_order`,
  `_pick`) que antes.
- Por cada tipo del día: si ya existe `(planned_for = día, batch_slot = tipo)`
  → «ya existía», sin importar su estado. Si no, elige la combinación con
  datos → «creada» (`--execute`) o «prevista» (en seco); si ninguna tiene
  datos → «sin datos».
- El borrador lleva `params.data_through = día − 1`: la misma cifra con tope
  que antes.
- Una carrera entre dos corridas la frena la restricción única: la segunda
  inserción falla dentro de un savepoint y se informa «ya existía».
- `--execute` corre la retención. En seco no escribe nada.
- Ya no rasteriza ni levanta Chromium. `--out-dir` se va: la carpeta sale de
  `SAIMC_MARKETING_DIR` (por defecto `var/marketing`).
- Sale con 0 si al menos un tipo quedó «creada», «prevista» o «ya existía», y
  con 1 si todos dieron «sin datos».

### 1.4 Retención

Se aplica con fecha `today` (el día de la corrida):
- `published` → nunca se borra.
- `approved` sin publicar → se conserva (el plan no las nombra).
- `draft` con `(today − created_at.date()) > 30` → se borra.
- `discarded` con `(today − discarded_at.date()) > 30` → se borra.
- Una fila de exactamente 30 días se queda; una de 31 se va.
- Al borrar una publicación se van sus diapositivas (cascada) y su carpeta de
  imágenes.
- Las carpetas viejas `var/marketing/AAAA-MM-DD/` del lote anterior se siguen
  purgando con la regla de siempre (`purge_old`).

### 1.5 Pantallas y rutas (todas detrás de `require_operator`)

| Ruta | Qué hace |
|---|---|
| `GET /marketing` | **Hoy**: publicaciones con `planned_for` = hoy en La Habana, con miniatura, estado y acción principal. Avisa por cada tipo del calendario que falta (con botón «Crear»). «+ Nueva» con elección de tarjeta. Las piezas fijas van plegadas abajo, con sus descargas de siempre |
| `POST /marketing/post` | crea un borrador del operador (`kind`) → 303 al editor |
| `GET /marketing/post/{id}` | editor. En borrador: producto, provincia, fecha de corte, bloques, textos y texto del post. En los otros estados: solo lectura, con las acciones que permite el estado |
| `POST /marketing/post/{id}` | guarda parámetros, opciones y texto. Con `HX-Request` devuelve la vista parcial; sin HTMX, 303 al editor |
| `GET /marketing/post/{id}/preview/{pos}` | HTML de la diapositiva: en vivo si es borrador, desde el snapshot si no |
| `GET /marketing/post/{id}/slide/{pos}.jpg` | el JPEG congelado; 404 si no hay (borrador o descartada) |
| `POST /marketing/post/{id}/aprobar` · `/descartar` · `/publicada` · `/duplicar` | la transición → 303. 409 si no está permitida, 422 si no hay datos o el enlace no sirve, 503 sin Chromium |

Se quedan sin cambios `GET /marketing/preview/{slug}` y
`/marketing/card/{slug}.{png,jpg}`, que usan las piezas fijas. Se va
`GET /marketing/vista` (el editor sin estado de F1).

Compartir solo sale de una publicación aprobada: lo que se publica es siempre
lo congelado.

### 1.6 Fuera de alcance

Carruseles de varias diapositivas (F3), semana e historial (F4), métricas (F5),
publicaciones atrasadas de días anteriores en «Hoy».

### 1.7 Casos de prueba (escritos antes del código)

1. Transiciones: las cuatro permitidas cambian el estado y su marca de
   tiempo. Las prohibidas lanzan `InvalidTransition` sin tocar la fila:
   draft→published, published→discarded, published→approved,
   discarded→approved, discarded→published, approved→approved. Editar una
   aprobada falla. Publicar con un enlace vacío o `javascript:` falla.
2. Snapshot: aprobar un «Dato del día» de Arroz a 300–350 → corregir en la
   base la fila de 350 a 999 → la vista previa de la publicación y el texto
   congelado siguen diciendo 350 y no dicen 999. El JPEG se pidió con el HTML
   del snapshot. Escribir en `snapshot` u `options` después de aprobar lanza
   `FrozenSlideError`. Ida y vuelta: serializar y reconstruir da dataclasses
   iguales.
3. Aprobar sin Chromium deja el borrador intacto, sin snapshot y sin archivo.
4. Lote: dos corridas `--execute` del mismo lunes → exactamente un borrador
   de `dato-del-dia` y uno de `como-se-movio`, y la segunda informa «ya
   existía». Descartar el de `dato-del-dia` y volver a correr → no se crea
   otro. En seco no crea nada. El borrador lleva `data_through` = domingo.
   Sin anuncios → «sin datos», con salida 1. Insertar dos veces el mismo
   `(planned_for, batch_slot)` viola la restricción.
5. Retención con `today` fijo: un borrador de 30 días se queda y uno de 31
   se va junto con su carpeta; una descartada se mide por `discarded_at`,
   no por `created_at`; una publicada de 400 días se queda; una aprobada de
   60 días se queda.
6. Rutas: «Hoy» lista solo lo de hoy y avisa lo que falta; crear redirige al
   editor; guardar persiste opciones y texto; un bloque sin interruptor no se
   apaga ni guardado; el JPEG de un borrador da 404; aprobar sin datos da
   422; descartar una aprobada borra su archivo.

## 2. Diseño

### 2.1 Módulos

| Archivo | Qué tiene |
|---|---|
| `models/marketing_post.py` | `MarketingPost`, `MarketingPostSlide`, los `StrEnum` y el listener de inmutabilidad |
| `alembic/versions/0033_marketing_posts.py` | las dos tablas; sin relleno, no toca nada existente |
| `marketing/snapshot.py` | `freeze_context` / `thaw_context`: un registro cerrado de las dataclasses de `cards.py`. Un `__type__` desconocido es un error, no un `dict` suelto |
| `marketing/html.py` | `card_html(kind, context, options)`: el entorno Jinja propio que ya usaba el lote, compartido por el lote, la aprobación y el editor |
| `marketing/posts.py` | dominio: `create_draft`, `save_draft`, `approve`, `discard`, `mark_published`, `duplicate`, `purge_expired`, `effective_caption`. No conoce FastAPI. Recibe `render` e `images_dir` como argumentos, así los tests no levantan Chromium |
| `marketing/options.py` | `options_to_dict` / `options_from_dict`: al leer se vuelve a sanear, así que un bloque sin interruptor sigue prendido aunque el JSON diga otra cosa |
| `marketing/daily.py` | `run_daily` crea borradores y corre la retención |
| `web/routers/marketing.py` | Hoy, editor, transiciones; las rutas de las piezas fijas quedan como estaban |
| `config.py` | `marketing_dir: Path = Path("var/marketing")` |

### 2.2 Texto del post

`effective_caption(post)` devuelve `post.caption` si existe; si no, el texto
generado (`build_caption`) desde el contexto en vivo (borrador) o desde el
snapshot (aprobada). Al guardar, si el texto enviado coincide con el que se
habría generado con los parámetros **anteriores**, se guarda nulo y sigue
automático. Así, cambiar de producto no deja pegado el texto del producto
viejo. Al aprobar se escribe el texto efectivo en `post.caption` y en el
snapshot.

### 2.3 Rutas de imagen

`<marketing_dir>/posts/<post_id>/<position>.jpg`. La columna guarda
`<post_id>/<position>.jpg`. Descartar y purgar borran la carpeta de la
publicación entera.

### 2.4 Descartado en el diseño

- **JSONB**: los tests corren sobre SQLite y no hay consultas dentro del JSON;
  `sa.JSON` alcanza.
- **Snapshot como HTML ya pintado**: atado a la plantilla de ese día; con el
  contexto se puede volver a dibujar y auditar cada cifra.
- **Guardar la imagen en la base**: el disco ya es el lugar de los activos, y
  una fila de cientos de kB por diapositiva infla los respaldos.
