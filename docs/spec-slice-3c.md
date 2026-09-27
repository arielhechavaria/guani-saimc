# Slice 3c (parte 1) — Glosario del operador

Parte del plan `docs/plan-asistente-llm-cola-completa.md`, slice 3c. Toca
esquema (tabla nueva) y el armado del prompt: esta especificación se aprueba
antes de programar. Cubre solo el glosario — el recorte del texto de entrada
(la otra mitad de 3c) queda fuera, sin investigación pendiente que lo bloquee
(ver «Qué no entra»).

## Problema

La nota del operador (slice 3a, campo `llm_reviews.note`) es un dato que se
pierde en el anuncio que la generó: si en el anuncio 500 el operador escribe
«4x10 = 4 paquetes de 10 lb, la caja pesa 40 lb», el modelo sigue sin saberlo
en el anuncio 501. El ejemplo ya está hardcodeado a mano en `_SYSTEM_PROMPT`
(`llm_propose_ad.py`, regla de `price_basis`) porque no hay otro lugar donde
ponerlo. Promover una nota a regla general hoy exige editar código y
desplegar.

## Qué se construye

### Tabla `glossary_entries`

| Columna | Tipo | Notas |
| --- | --- | --- |
| `id` | UUID PK | |
| `term` | varchar(120), not null | ejemplo: «4x10» |
| `definition` | text, not null | ejemplo: «4 paquetes de 10 lb; la caja pesa 40 lb» |
| `active` | bool, not null, default true | una entrada se desactiva, no se borra sin razón |
| `created_at` | timestamptz, not null | |

A diferencia de `llm_proposals`/`llm_reviews`, esta tabla **sí se edita**: es
configuración curada por el operador, no un registro de lo que pasó. Editar o
desactivar una entrada cambia lo que ven las llamadas siguientes; no reescribe
ninguna llamada ya hecha (eso vive en `llm_proposals.prompt_version`, ver
abajo).

### CRUD en `/catalog`

Mismo patrón que `ProductFormat` (`web/routers/catalog.py`): alta, edición y
borrado, `require_operator`, redirect 303 con flash por querystring. Sin JS.
Un término o definición vacíos se rechazan con error.

### Inyección en el prompt

`build_prompt_blocks` (`llm_propose_ad.py`) recibe `glossary:
Sequence[GlossaryEntry] = ()` y, si hay entradas activas, agrega un bloque
`"Glosario del operador:\n- «term»: definition\n..."` **dentro del mismo
bloque cacheado que los catálogos** (mismo `cache_control: ephemeral`): el
glosario cambia con la misma frecuencia que el catálogo de productos, no con
cada anuncio. Sin entradas activas, no se agrega texto — el requisito del plan
de «glosario corto» empieza por no gastar tokens cuando está vacío.

### Versión efectiva del prompt

`PROMPT_VERSION` sigue siendo la constante que se sube a mano cuando cambia el
texto de `_SYSTEM_PROMPT`. Se agrega `effective_prompt_version(base, entries)`
en `saimc/glossary.py`:

- Sin entradas activas: devuelve `base` sin cambios (una corrida sin glosario
  se ve igual que hoy; no hay ruido en el historial de nadie que no lo usa).
- Con entradas: devuelve `f"{base}+glossary:{hash[:8]}"`, con `hash` un
  `sha256` de `(term, definition)` de cada entrada activa, ordenadas por
  `term` (el orden no puede depender de cuándo se insertaron).

Esto reemplaza el requisito del plan («cada cambio del glosario cambia la
versión del prompt») por un mecanismo automático: el operador edita el
glosario desde `/catalog` sin acordarse de tocar código, y dos corridas con
distinto contenido de glosario quedan con `prompt_version` distinto en
`llm_proposals`, comparables en el backtest.

`AdProposal` gana el campo `prompt_version: str = PROMPT_VERSION`, calculado
por `propose_ad` en cuanto arma la llamada (glosario recibido como
parámetro) y usado tanto en el registro de invocaciones
(`append_assist_run`) como en `llm_store.save_proposal` (que deja de importar
la constante y lee `result.prompt_version`).

### Sin dato semilla

El ejemplo de «Pollo Muslo 4x10» **no se saca** de `_SYSTEM_PROMPT`: ese texto
ya está backteado y fijado por `test_el_prompt_define_mayorista_base_y_envase_como_el_formulario`
(prompt `2026-09-27.1`, formato de venta 97,1%). Migrarlo no aporta nada — el
glosario es para notas nuevas que hoy no tienen dónde ir, no para reescribir
un prompt ya medido y en producción. La tabla arranca vacía; el operador la
puebla desde `/catalog` cuando aparezca el primer caso real que lo justifique.

## Qué no entra

- El recorte del texto de entrada (la otra mitad de 3c): sigue pendiente,
  sin bloqueo — se retoma aparte.
- Aprender del glosario para otros módulos (`llm_assist.propose_missing`
  sigue con su propio `_SYSTEM_PROMPT`, sin glosario): fuera de alcance,
  el plan solo pide `propose_ad`.
- Historial de cambios del glosario (quién editó qué y cuándo): con edición
  simple alcanza; si hace falta auditar se agrega después.

## Descartado

- **Versión a mano** (opción 2 de la propuesta al operador): el operador sube
  `PROMPT_VERSION` cada vez que edita el glosario. Descartado porque el plan
  exige que TODO cambio del glosario cambie la versión, y depender de la
  memoria de alguien no lo garantiza.
- **Glosario en el bloque del anuncio** (sin cachear): rompería el
  `cache_control` actual y encarecería cada llamada por el tamaño del
  glosario, que es justamente lo que el plan pide mantener chico pero no
  gratis.

## Casos de prueba (se escriben antes que el código)

1. `build_prompt_blocks` sin entradas de glosario arma el mismo prompt que
   hoy (sin sección de glosario).
2. `build_prompt_blocks` con una entrada activa agrega la sección al bloque
   cacheado, no al bloque del anuncio.
3. `build_prompt_blocks` con una entrada **inactiva** no la incluye.
4. `effective_prompt_version` sin entradas devuelve `base` sin cambios.
5. `effective_prompt_version` con las mismas entradas (mismo `term`,
   `definition`, distinto orden de inserción) devuelve el mismo hash.
6. `effective_prompt_version` con una entrada editada (mismo `term`, otra
   `definition`) devuelve un hash distinto.
7. `propose_ad` con glosario activo guarda ese `prompt_version` efectivo en
   el `AdProposal` devuelto y en la línea de `append_assist_run`.
8. `propose_ad` degradado por `no_key`/`empty_text` (sin llamada) guarda
   `PROMPT_VERSION` base, no el efectivo — no hubo llamada que lo use.
9. `llm_store.save_proposal` persiste `result.prompt_version`, no la
   constante del módulo.
10. Alta de una entrada con término vacío se rechaza con error, sin crear
    fila.
11. Editar una entrada existente cambia `term`/`definition` en el lugar
    (no crea una fila nueva).
12. Desactivar una entrada la saca del prompt de la próxima llamada sin
    borrarla.
13. La migración `0023` crea la tabla (vacía) y revierte limpio
    (upgrade/downgrade contra SQLite, mismo patrón que
    `TestProductVariantsMigration`).

## Registro

Entrada en `docs/decisions.md`: el glosario vive en una tabla editable, arranca
vacía (sin migrar el ejemplo ya backteado de `_SYSTEM_PROMPT`), y la versión
efectiva del prompt se deriva sola por hash del contenido activo.
