# Slice 3a — Guardar la propuesta del modelo y capturar la revisión

Parte del plan `docs/plan-asistente-llm-cola-completa.md` (slice 3, partido en
3a / 3b / 3c el 2026-09-25). Toca esquema: esta especificación se aprueba antes
de programar.

## Problema

1. La propuesta del modelo no se guarda: vive solo en el JavaScript de la página.
   Recargar vuelve a pagar la llamada, y al guardar la curaduría nadie sabe qué
   propuso el modelo ni qué corrigió el operador.
2. La respuesta cruda se tira después de parsearla. No se puede ver en qué se van
   los tokens de salida (ejemplo: 2629 tokens para 4 filas).
3. No hay dónde dejar una nota sobre lo que el modelo no podía saber
   («4x10» = 4 paquetes de 10 lb).

## Qué se construye

### Tabla `llm_proposals` (una fila por llamada, inmutable)

| Columna | Tipo | Notas |
|---|---|---|
| `id` | UUID PK | |
| `ingestion_id` | UUID FK → `raw_ingestions.id`, RESTRICT, índice | |
| `model` | varchar(64) | `claude-sonnet-5` |
| `prompt_version` | varchar(64) | `PROMPT_VERSION` al momento de la llamada. Era `varchar(32)`; lo ensanchó la migración 0027 (K6, 2026-10-03) |
| `status` | varchar(16), CHECK `ok`/`degraded` | |
| `failure` | varchar(32) null | etiqueta de `classify_failure` si degradó |
| `discard` | bool null | veredicto del modelo |
| `discard_reason` | varchar(16) null, CHECK `off_focus`/`no_data` | |
| `rows` | JSON | lista de filas: `product_id`, `price` (texto decimal), `currency`, `quantity`, `unit`, `province_id`, `evidence`, `currency_explicit`, `evidence_found`, `price_in_evidence` |
| `raw_response` | text null | respuesta del modelo tal cual; el modelo solo ve texto redactado, así que no trae teléfonos |
| `input_tokens`, `output_tokens` | int | |
| `created_at` | timestamptz | |

Nunca se actualiza ni se borra: el código no tiene camino de escritura sobre una
fila existente. Pedir otra propuesta crea otra fila.

### Tabla `llm_reviews` (una fila por anuncio revisado con propuesta a la vista, inmutable)

| Columna | Tipo | Notas |
|---|---|---|
| `id` | UUID PK | |
| `ingestion_id` | UUID FK → `raw_ingestions.id`, RESTRICT, índice | |
| `proposal_id` | UUID FK → `llm_proposals.id`, RESTRICT | la propuesta que se mostró |
| `outcome` | varchar(16), CHECK `curated`/`discarded` | |
| `discard_reason` | varchar(16) null | el que eligió el operador |
| `note` | text null | nota libre del operador, con teléfonos redactados |
| `rows` | JSON | una entrada por fila propuesta y por fila agregada (ver abajo) |
| `created_at` | timestamptz | |

Cada entrada de `rows`:

- `proposal_index`: índice de la fila en la propuesta, o `null` si el operador la
  agregó a mano.
- `price_history_id`: la fila guardada, o `null` si el operador borró la propuesta.
- `proposed`: los seis campos que propuso el modelo (o `null`).
- `final`: los seis campos que confirmó el operador (o `null` si la borró).
- `edited`: lista de los campos donde `proposed` y `final` difieren.

«Final» es lo confirmado **al guardar la curaduría**. Una edición posterior por
`/curation/{id}/edit` no toca la captura.

### Cambios en la curaduría

- **«Completar con IA»** guarda la propuesta en `llm_proposals` (también si
  degradó, para registrar la falla) y la muestra.
- **Abrir un anuncio** (GET) con propuesta `ok` guardada la muestra, **sin llamar a
  la API**. El botón sigue disponible para pedir otra.
- El formulario lleva ocultos `proposal_id` y, en cada fila que vino del modelo,
  `proposal_row_N`. Es así como el guardado sabe qué se propuso y qué se tocó.
- **Campo «Nota para el asistente»** (opcional, plegado) visible solo cuando hay
  propuesta.
- **Guardar** o **descartar** con `proposal_id` presente escribe una fila en
  `llm_reviews`. Sin propuesta, nada cambia respecto de hoy.
- **H0:** un anuncio cuenta como asistido si está en el log (como hoy) **o** tiene
  una fila en `llm_reviews`. En 3b el log deja de alcanzar.

## Qué no entra

- El pre-cálculo por lotes (3b).
- Promover la nota al glosario y recortar el texto (3c).
- Leer la captura en `/control` (slice 5).

## Descartado

- **Una fila por campo o por fila revisada** en vez de JSON por anuncio: da
  claves foráneas en todo, pero duplica la escritura y las consultas. Con este
  volumen (~1200 anuncios al mes), leer el JSON en Python alcanza.
- **Guardar la propuesta en un campo oculto del formulario** en lugar de en una
  tabla: el operador podría alterarla y no sobrevive a una recarga.

## Casos de prueba (se escriben antes que el código)

1. «Completar con IA» con respuesta falsa crea una fila `ok` con filas, tokens y
   respuesta cruda.
2. Una falla crea una fila `degraded` con su etiqueta, y el formulario queda como
   lo armaron las reglas.
3. Abrir un anuncio con propuesta guardada no llama al cliente de la API y
   muestra las filas del modelo.
4. Guardar sin tocar nada: `edited` vacío en cada fila y `price_history_id`
   enlazado.
5. Editar el precio de una fila: `edited == ["price"]` solo en esa fila.
6. Borrar una fila propuesta: `final` y `price_history_id` en `null`.
7. Agregar una fila a mano: `proposal_index` en `null`.
8. Descartar con propuesta a la vista: `outcome = discarded` con el motivo
   elegido.
9. La nota se guarda con los teléfonos redactados.
10. Sin `proposal_id` no se escribe `llm_reviews`.
11. Un `proposal_id` que no es de ese anuncio se rechaza: no se escribe la
    captura y la curaduría sí se guarda.
12. H0 excluye un anuncio que tiene `llm_reviews` aunque no esté en el log.
13. La migración 0021 crea y elimina las dos tablas (upgrade y downgrade).

## Registro

Entrada en `docs/decisions.md`: la propuesta y la revisión se guardan en tablas
propias e inmutables, el valor final es el del primer guardado, y H0 excluye
también por `llm_reviews`.
