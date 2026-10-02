# Plan — Sacar al operador del camino de cada registro

Fecha del plan: 2026-09-29. Última actualización: 2026-10-02 (A6 desbloqueada y
ampliada a los campos cerrados; A6b agregada; contrato de la señal externa en A5a;
columna «Modelo» en el tablero; A2a y A2b cerradas; regla de actualizar el estado al
terminar cada tarea).

El **qué** y el **por qué** están en `docs/estudio-factor-humano.md`. Este plan dice
en qué orden se hace y cómo se cierra cada paso. Cuando aparece «estudio §4»,
remite a una sección de ese documento. «§6.2» a secas remite a `docs/spec.md`.

---

## Tablero (empezá por acá)

**Regla de cierre**, la de `CLAUDE.md`. Un slice está cerrado cuando se cumplen
seis cosas:
1. los tests pasan;
2. está la entrada en `docs/decisions.md`, si corresponde;
3. pasó `docs/checklist-pr.md`;
4. **el estado del slice está actualizado en este plan**, en la fila del Tablero y
   en el título de su sección, dentro del mismo commit;
5. el commit local está hecho;
6. se entregaron los pasos de verificación.

**El plan se actualiza al terminar cada tarea, sin excepción.** Un slice terminado
que sigue figurando `PENDIENTE` no está cerrado. Al abrirlo pasa a `ABIERTO`; al
terminarlo, a `CERRADO (fecha)`. Si se bloquea o se descarta, se anota el motivo
en el mismo momento.

El push, la migración en el VPS y la verificación los hace el operador. El
siguiente slice no se abre hasta que el operador confirme.

**Estados:** `PENDIENTE`, `ABIERTO`, `CERRADO (fecha)`, `BLOQUEADO (motivo)`,
`DESCARTADO (motivo)`.

**Tamaño:** S es una sesión corta, M una sesión completa, L dos sesiones o más
(se parte si crece).

**Modelo:** con cuál se abre la sesión (`/model`). Opus donde un error se paga en
migraciones, datos reprocesados o una conclusión estadística equivocada:
especificaciones, motor de validación, esquema, calibración, privacidad y carriles
automáticos. Sonnet donde la decisión ya está tomada y queda implementar. Es un
criterio, no una medición: si un slice de Sonnet se traba, se pasa a Opus en la
misma sesión. El candidato más probable es A1b.

| Slice | Qué | Tamaño | Depende de | Modelo | Estado |
| --- | --- | --- | --- | --- | --- |
| A0 | Base confiable: suite verde y cifras de costo al día | S | — | Sonnet | CERRADO (2026-10-02) |
| A2a | Bitácora de curaduría: especificación | M | A0 | Opus | CERRADO (2026-10-02) |
| A2b | Bitácora de curaduría: migración y escritura | M | A2a | Sonnet | CERRADO (2026-10-02) |
| A2c | Métrica «Aceptación sin revisar» corregida | M | A0 | Sonnet | CERRADO (2026-10-02) |
| A1a | Lote de imágenes: prototipo a 390 px | S | — | Sonnet | CERRADO (2026-10-02) |
| A1b | Lote de imágenes con el veredicto del modelo | M–L | A1a, A2b | Sonnet | CERRADO (2026-10-02) |
| A7 | Fecha de las fotos de WhatsApp prellenada | S | A1b | Sonnet | CERRADO (2026-10-02) |
| A3a | Glosario: cargar las convenciones del operador | S | — | Sonnet | CERRADO (2026-10-02) |
| A3b | Glosario: medir su efecto con el backtest | S | A3a | Sonnet | PENDIENTE |
| A4a | Panel por canal que sirva para decidir | M | A2b | Sonnet | PENDIENTE |
| A4b | Decidir la política de cada canal | S | A4a | Sonnet | PENDIENTE |
| A4c | (condicional) Canal «solo texto» | M | A4b | Sonnet | PENDIENTE |
| A2d | Panel «Horas y automatización» en `/control` | M | A2b y una semana de datos | Sonnet | PENDIENTE |
| A5a | Motor de validación §6.2 y carriles: especificación | L | A2c | Opus | PENDIENTE |
| A5b | Motor de validación en código puro, sin esquema | L | A5a | Opus | PENDIENTE |
| A5c | Informe del modo sombra | M | A5b | Opus | PENDIENTE |
| A3c | «Guardar como regla» desde la cola | M | A3b | Sonnet | PENDIENTE |
| A1c | (opcional) Lote de textos en foco que el modelo descarta | M | A1b | Sonnet | PENDIENTE |
| A6 | Prueba de Jev fuera de línea: triage y campos cerrados | M | — | Opus | PENDIENTE |
| A6b | Jev como señal desconectable del motor, en modo sombra | M–L | A6 favorable, A5b, B1 | Opus | PENDIENTE |
| K1 | `/edit` con historial | M | A2b | Sonnet | PENDIENTE |
| K3 | Guardar la transcripción del OCR | M | — | Sonnet | PENDIENTE |
| K4 | Aviso de hueco de ingesta por canal | M | — | Sonnet | PENDIENTE |
| K6 | Ensanchar `llm_proposals.prompt_version` | S | próxima migración | Sonnet | PENDIENTE |
| B1 | Persistir la validación: estado, puntaje y procedencia | L | A5b | Opus | PENDIENTE |
| B5 | Emisor de grupo como hash con clave | M | — | Opus | PENDIENTE |
| **G1** | **Compuerta: ¿se automatiza?** | S | A2d, A5c con n suficiente (A6 y A6b, si hubo) | Opus | PENDIENTE |
| B2a | Carril 0 encendido: descarte automático con auditoría | M | G1, B1 | Opus | PENDIENTE |
| B2b | Carril 1 encendido: aceptación automática con auditoría | L | G1, B1 | Opus | PENDIENTE |
| B2c | Freno automático del carril 1 | M | B2b | Opus | PENDIENTE |
| B3 | Conjunto de referencia congelado y métrica de Fase 0 redefinida | M | G1 | Opus | PENDIENTE |
| B4 | Capa pública: por qué confiamos en cada dato (§6.5) | M–L | B1, B2b | Sonnet | PENDIENTE |
| C1 | Temporizador del pre-cálculo, y lotes de la API cuando convenga | M | B2a | Sonnet | PENDIENTE |
| C2 | Imágenes con modelo de visión y redacción previa | L | K3, G1 | Opus | PENDIENTE |
| C3 | Controles de §10.7 (C1–C4) antes de sumar fuentes | L | B5 | Opus | PENDIENTE |
| C4 | Expansión: provincias, productos y canales | por tanda | C3 | Sonnet | PENDIENTE |

### Próximos pasos, en orden

Estimación: a un slice por día hábil, la fase A son unas cuatro semanas. El modo
sombra acumula datos durante tres a seis semanas más, que se aprovechan con los K,
B1 y B5. La compuerta cae entre la semana 6 y la 8. Son estimaciones.

1. **A0** — sin suite verde no se puede cerrar ningún slice.
2. **A2a → A2b** — medir antes de cambiar: la línea de base del tiempo humano
   empieza a juntarse acá.
3. **A2c** — la métrica de sello automático tiene que decir la verdad antes de
   empezar a mirarla todos los días.
4. **A1a → A1b** — el bloque más grande del tiempo (estudio §1: 32–41%). Entre A2b y
   A1b pasan unos días de línea de base con la rutina vieja.
5. **A7** — va después de A1b para no mezclar dos cambios en la misma medición.
   *Nota (2026-10-02):* el operador decidió abrir A7 el mismo día en que cerró A1b,
   sin esperar días de línea de base. La medición de las semanas siguientes mezcla
   el efecto de A1b y el de A7; no se puede atribuir cada uno por separado.
6. **A3a → A3b** — el glosario ataca la primera causa de edición.
7. **A4a → A4b (→ A4c)** — política por canal.
8. **A2d** — para entonces ya hay una semana larga con datos antes y después de A1b.
9. **A5a → A5b → A5c** — el motor en modo sombra. Desde A5c, una corrida por semana
   hasta llegar al n de la especificación.
10. **A3c, A1c** — mejoras de la cola mientras el modo sombra acumula.
11. **K1, K3, K4, B1, B5** — lo que se construye durante la acumulación. Ninguno
    necesita la compuerta.
12. **G1** — la decisión.
13. **B2a → B2b → B2c → B3 → B4**, y después la fase C.

A6 ya tiene acceso a Jev (2026-10-02) y entra donde caiga. No bloquea nada, pero
conviene cerrarla antes de A5a: su resultado dice si el contrato de la señal externa
se especifica con Jev en mente o sin él. A6b va después de B1 y solo si A6 salió
favorable; tampoco bloquea nada, y G1 se puede decidir sin ella.

### Hitos para el inversor

| Hito | Cuándo | Qué se muestra |
| --- | --- | --- |
| I1 | después de A2d | Horas humanas por 1.000 anuncios, medidas antes y después del lote de imágenes |
| I2 | después de A5c con n suficiente | «El sistema podría resolver solo el X%, con un error de a lo sumo Y% (cota al 95%)», medido y no prometido |
| I3 | cuatro semanas después de B2b | Automatización real, con el error auditado semana a semana |

### Pendientes sueltos

Lo que aparezca fuera del slice en curso se anota acá, no se hace.

- `catalog.delete_format` pone `format_id` en nulo en bloque sobre `prices_history`
  sin dejar rastro. Los backfills por línea de comandos tampoco (el de tasas no
  tiene log). Fuera de la bitácora de A2 (visto en A2a, 2026-10-02).
- Después de editar o eliminar un curado, `llm_reviews.rows` queda desfasado
  (`price_history_id` puede apuntar a una fila borrada). Es por diseño desde el
  slice 3a; revisar si K1 o A2c lo necesitan (visto en A2a, 2026-10-02).

---

## Cómo se trabaja cada sesión

**Al empezar:**
1. `mem_context`, como pide `CLAUDE.md`.
2. Abrir este plan, ir al Tablero y tomar el primer `PENDIENTE` de «Próximos pasos».
   Si el modelo de la sesión no es el de la columna «Modelo», avisarlo antes de
   empezar.
3. Confirmar que el slice anterior está `CERRADO` y verificado por el operador, y que
   `git status` está limpio.
4. Si el slice toca el esquema o el motor de validación, la sesión arranca por la
   especificación, o la especificación ya está aprobada.

**Durante:**
- En la capa de extracción y validación, el caso esperado se escribe antes que el
  código y nunca se deriva del comportamiento del código.
- Lo que aparezca fuera del slice va a «Pendientes sueltos».
- Todo gasto de API se aprueba antes de correrlo, con el monto estimado.
- Si una pantalla cambia de forma visible, se diseña primero a 390 px y el prototipo
  se aprueba antes de programar.

**Al cerrar:**
1. `.venv/bin/pytest` (~100 s), `.venv/bin/ruff check .` y `.venv/bin/mypy`, todo en
   verde.
2. La entrada en `docs/decisions.md`, si corresponde.
3. `docs/checklist-pr.md`.
4. Actualizar el estado del slice en este plan (fila del Tablero y título de la
   sección) y commitearlo junto con el trabajo. Es obligatorio en cada cierre.
5. Los pasos de verificación: URL, qué mirar, qué tiene que pasar y qué no. Si hay
   migración, primero `pg_dump` y después `alembic upgrade head`.

**Después:** el operador hace push, migra si hace falta, reinicia `saimc.service`,
verifica y confirma.

**La rutina diaria sigue en paralelo:** pre-cálculo y curaduría, según
`docs/runbook-rutina-diaria.md`. El modo sombra necesita esas revisiones.

---

## Qué cambia y qué no

**Cambia:**
- el operador deja de tocar uno por uno lo que el modelo ya resolvió bien;
- se mide el tiempo humano;
- el motor de §6.2 existe y deja constancia de por qué confía en cada dato.

Después de la compuerta, y solo si el operador lo decide, parte de los anuncios se
resuelve sin revisión individual, con auditoría.

**No cambia:**
- el stack (decisión cerrada 1);
- sin Redis ni Celery (decisión cerrada 3): un temporizador no es una cola;
- sin CI/CD (decisión cerrada 4);
- `raw_ingestions` inmutable, salvo los metadatos de procesamiento;
- la privacidad de §10.6;
- el push lo hace el operador;
- un slice por sesión.

## Decisiones del operador (2026-09-29)

- El estudio es la guía; este plan fija el orden.
- **Todo proveedor externo es desconectable.** Si falla, cierra o se deja de pagar,
  el sistema sigue funcionando con su calibración base. Vale para Jev y para
  cualquier otro. Registrado en `decisions.md` el 2026-09-29.
- Jev solo como prueba fuera de línea (A6). No entra en producción antes de pasarla.
- **2026-10-02.** Hay clave de Jev. A6 no se reemplaza: es la medición de la que
  dependen los umbrales. Si sale favorable, Jev entra por A6b como una señal más del
  motor, en modo sombra. No decide ni escribe `origin = auto`: eso sigue siendo de
  B2b, después de G1.
- La automatización (carriles 0 y 1) no se enciende sin la compuerta G1.

## Apartamientos que este plan va a pedir

Ninguno se toma en este plan. Cada uno se decide en su slice y se registra ahí.

| Qué | De qué | Dónde se decide |
| --- | --- | --- |
| Carriles 0 y 1 encendidos | decisión cerrada 2; §6.1 («no etapas autónomas»); §10.7 (un humano por registro en la Fase 0–1) | G1 |
| Usar `robust_fence` para validar | su docstring la declara «de dibujo, no de publicación» | A5a |
| Banda robusta en lugar de la fórmula 70/30 de la regla 2, si se elige así | §6.2 regla 2 | A5a |
| Métrica de la Fase 0 | §12.5 y §3, y la entrada del 2026-08-12 | B3 |
| Pre-cálculo con temporizador | el motivo escrito de la decisión cerrada 3 | C1 |
| Imágenes fuera del VPS, redactadas | «la imagen nunca sale del VPS» (`decisions.md`, 2026-09-01) y §10.6 | C2 |

---

## Fase A — sin apartarse de nada

### A0 — Base confiable: suite verde y cifras de costo al día — CERRADO (2026-10-02)

**Objetivo.** Que «los tests pasan» signifique algo en cada cierre, y que los
documentos de la rutina no citen un costo viejo.

**Hechos de partida.**
- Hoy, en el VPS, fallan 2 tests (verificado el 2026-09-29):
  `tests/test_marketing_router.py::TestDescargaPNG::test_sin_chromium_instalado_degrada_a_503_explicando_como_instalarlo`
  y `…::test_una_tarjeta_estatica_tambien_degrada_a_503_sin_chromium`.
- Esperan un 503 y reciben 200 porque la máquina sí tiene Chromium.
- `docs/costos.md` y `docs/runbook-rutina-diaria.md` citan $0,0128 por anuncio. El
  costo real, desde que se apagó `thinking`, es de $0,0015 (imagen) a $0,0029
  (texto).

**Qué entra.**
1. Los 2 tests: simular la ausencia de Chromium (monkeypatch del import perezoso o del
   lanzamiento en `saimc/marketing/render.py:render_png`) en vez de depender de la
   máquina.
2. `docs/costos.md`: recalcular con `llm_proposals` (la consulta se vuelve a correr
   en la sesión, no se copian las cifras de acá).
3. `docs/runbook-rutina-diaria.md`: la cifra de costo, y el `--max-cost` si cambia.

**Qué no entra.** Código de producción.

**Tests primero.** Los 2 tests tienen que pasar con Chromium y sin él. Se prueba
desinstalándolo en un entorno de prueba, o forzando la rama sin él.

**Verificación del operador.**
- En el VPS, `.venv/bin/pytest -q` da 0 fallos.
- `docs/costos.md` muestra la tabla nueva con la fecha.

**`decisions.md`.** Sí, breve: el costo recalculado y el motivo del cambio de los
tests.

---

### A2a — Bitácora de curaduría: especificación — CERRADO (2026-10-02)

La especificación es `docs/spec-slice-A2.md`. Corrige dos de los hechos de partida
de abajo y trae tres preguntas abiertas con respuesta por defecto.

**Objetivo.** Definir una tabla de solo altas que registre cada decisión del
operador con su hora y su duración. Es la base de tres cosas: las horas por 1.000
anuncios (A2d), la auditoría (B2) y el historial de `/edit` (K1).

**Hechos de partida** (mapa del código, 2026-09-29):
- Ningún descarte guarda la hora.
- `discard_curation` (`web/routers/curation.py`) ignora el `shown_at` que el
  formulario sí le envía.
- `discard_out_of_focus` solo cambia estado y motivo.
- `delete_curated` y `submit_edit` no dejan rastro.
- `raw_ingestions` no tiene `updated_at`.
- `shown_at` hoy solo se guarda al curar (`llm_reviews.shown_at`) y solo si había una
  propuesta.

**Qué define la especificación.** Documento `docs/spec-slice-A2.md`, al estilo de
`docs/spec-slice-3a.md`:
- **Tabla** (nombre a decidir, por ejemplo `curation_events`):
  - `id` e `ingestion_id` (FK con RESTRICT);
  - `action`: `curated|discarded|deleted|edited`;
  - `mode`: `individual|batch_out_of_focus`, y más adelante `batch_model|auto`;
  - `discard_reason`, `proposal_id` (nulo), `shown_at`, `decided_at`;
  - `detail` en JSON (para `edited`: antes y después).
- **Qué caminos escriben:** `submit_curation`, `discard_curation`,
  `discard_out_of_focus`, `delete_curated` y `submit_edit`.
- **Relación con `llm_reviews`:** no la reemplaza. Las revisiones comparan propuesta
  y valor final; la bitácora registra la acción y su tiempo.
- **Pausas:** una duración absurda (el operador se levantó a mitad de un anuncio) se
  guarda tal cual. El recorte se decide al medir (A2d), no al escribir.
- **Migración** `0024_…`, sin backfill: el pasado no tiene hora.
- **Descartado** (para dejarlo escrito): una columna `decided_at` en
  `raw_ingestions`. Da una sola hora por anuncio, no distingue lote de individual y
  no sirve para auditar ni para el historial de `/edit`.

**Verificación del operador.** Leer la especificación y contestar sus preguntas
abiertas.

**`decisions.md`.** Sí.

---

### A2b — Bitácora de curaduría: migración y escritura — CERRADO (2026-10-02)

**Objetivo.** Que desde este slice cada acción del operador quede registrada.

**Qué entra.**
- La migración `0024_…` y el modelo.
- Un ayudante único para escribir el evento, llamado desde los cinco caminos de A2a.
- Todo según `docs/spec-slice-A2.md`, incluidos sus 17 casos de prueba. El campo
  oculto `shown_at` se suma a los tres formularios de descarte que hoy no lo llevan
  y a `/edit`; «Completar con IA» conserva el reloj.
- `discard_curation` pasa a aceptar `shown_at`.
- `/curation/sin-foco` manda la hora en que se mostró la lista.

**Tests primero.**
- Por cada camino, un evento con `action`, `mode` y tiempos correctos.
- Un `shown_at` malformado queda nulo y no rompe nada (igual que hoy al curar).
- Evento y cambio de estado van en la misma transacción: si uno falla, no queda el
  otro.
- El lote sin foco escribe un evento por anuncio.

**Verificación del operador.**
1. `pg_dump`, push, `alembic upgrade head` y reinicio.
2. En `/curation`, descartar un anuncio y curar otro.
3. `psql … -c "select action, mode, decided_at - shown_at from curation_events order
   by decided_at desc limit 5"` muestra 2 filas con duración.
4. Descartar en lote desde `/curation/sin-foco`: aparece un evento por anuncio, con
   `mode = batch_out_of_focus`.
5. Lo que no tiene que pasar: que curar o descartar se vuelva más lento o falle.

**`decisions.md`.** Sí. Desde acá corre la línea de base.

---

### A2c — Métrica «Aceptación sin revisar» corregida — CERRADO (2026-10-02)

**Objetivo.** Que la métrica que vigila el sello automático diga la verdad.

**Hechos de partida.** Hay cuatro errores confirmados en el código:
- `llm_store._edited` devuelve `[]` si la fila se borró, así que un rechazo cuenta
  como «sin editar».
- La provincia fija del canal que pisa la propuesta cuenta como edición del operador.
- Completar una abstención (el modelo propuso `None`) cuenta igual que corregir un
  valor.
- Los descartes no tienen `shown_at`.

Hoy la métrica muestra 80% (106/132); sin las filas borradas da 78,3% (94/120).

**Qué entra.**
- `web/rubber_stamp_metrics.py`: recalcular a partir de `proposed` y `final`, que
  están guardados, en vez de confiar en el `edited` guardado. Así también se corrige
  el pasado sin tocar filas inmutables.
- Cuatro categorías: sin cambios, completó un vacío, corrigió un valor, borró la
  fila.
- El artefacto de provincia se excluye.
- `/control` muestra el desglose.

**Tests primero.** Cinco revisiones sintéticas, una por categoría más el artefacto de
provincia, con el resultado esperado escrito antes.

**Verificación del operador.** En `/control`, «Aceptación sin revisar» muestra el
desglose y el total ya no cuenta las filas borradas como aceptadas.

**`decisions.md`.** Sí: la nueva definición.

---

### A1a — Lote de imágenes: prototipo a 390 px — CERRADO (2026-10-02)

**Objetivo.** Acordar cómo se ve y cómo se usa antes de programar (mobile first, y
verificar contra el diseño aprobado).

**Qué muestra.**
- Una grilla de miniaturas (`/evidence/{id}`). En cada una: el motivo del modelo
  (`off_focus`/`no_data`), el canal y la fecha, y un control «Rescatar» que la deja
  pendiente.
- Al tocar una miniatura se amplía.
- Un solo botón: «Descartar las N marcadas».
- Un aviso: «X imágenes sin propuesta: corré el pre-cálculo».
- El estado vacío.

**Qué se decide con el operador.**
- ¿Van marcadas por defecto? Da velocidad, pero con riesgo de sello automático. La
  mitigación es el «Rescatar» visible y la auditoría posterior.
- ¿Pestaña dentro de `/curation/sin-foco` o pantalla propia? Rige «ocultar, no
  convivir».

**Verificación del operador.** Abrir el prototipo en el teléfono y en escritorio, y
aprobarlo.

**Resultado (2026-10-02).** Aprobado: `docs/prototipos/a1a-lote-imagenes.html`.
- Van **marcadas por defecto**. El freno es «Rescatar» visible; la auditoría por
  muestra recién existe desde B2, no en A1b.
- Es un segmento «Textos | Imágenes» dentro de `/curation/sin-foco`, no una pantalla
  propia.
- Para A1b: el subset de iconos no trae `undo`; «Rescatar» va solo con texto, o se
  suma el icono al subset.

**`decisions.md`.** No; va con A1b.

---

### A1b — Lote de imágenes con el veredicto del modelo — CERRADO (2026-10-02)

**Objetivo.** Eliminar el bloque más grande del tiempo humano: descartar imágenes una
por una, que es el 32–41% del total según el estudio §1.

**Por qué no se aparta de nada.** El sistema propone y el operador confirma el lote
(§6.1). Hay precedente: `/curation/sin-foco` ya resolvió el 52% de todo lo resuelto.

**Qué entra.**
- **Rutas:** GET y POST nuevos en `web/routers/curation.py`, copiando la forma de
  `curation_out_of_focus` y `discard_out_of_focus`.
- **Plantilla:** según A1a, y la pestaña en `_curation_tabs.html`.
- **Selección:** anuncios `PENDING`, imagen, cuya última propuesta `ok` diga
  `discard = True`.
- **Revalidación:** el POST revalida cada id.
- **Escritura:**
  - `DISCARDED`, con el `discard_reason` del modelo;
  - `llm_reviews` con `outcome = DISCARDED` y `proposal_id`, para que el anuncio siga
    fuera de H0 como asistido;
  - un evento de bitácora con `mode = batch_model`.
- **Rutina:** `docs/runbook-rutina-diaria.md` suma el paso nuevo.

**Tests primero.**
- **No son elegibles:** un texto, una imagen sin propuesta, una propuesta degradada,
  una propuesta que no descarta, un anuncio ya descartado.
- **Rescatar y revalidar:** los rescatados quedan `PENDING`; un id ajeno se ignora.
- **Escritura:** se escriben la revisión y el evento; todo en una transacción.

**Verificación del operador.**
1. Push y reinicio.
2. Pre-cálculo en seco y después con `--execute --max-cost 1`.
3. Abrir la pantalla nueva en el teléfono: aparecen N miniaturas con su motivo.
4. Rescatar una y confirmar.
5. En `/curation/cola?foco=imagen`, la rescatada sigue y las demás no.
6. Lo que no tiene que pasar: que aparezca una imagen sin propuesta o un texto.

**`decisions.md`.** Sí: el primer lote con el veredicto del modelo, y por qué no se
aparta de §6.1.

---

### A7 — Fecha de las fotos de WhatsApp prellenada — CERRADO (2026-10-02)

**Objetivo.** Sacar una fricción fija de cada imagen.

**Hechos de partida.**
- `_curation_view` prellena `captured_at.date()` para el texto de WhatsApp, pero no
  para las imágenes, aunque el webhook guarda en `captured_at` la hora del mensaje.
- No hay motivo registrado: la entrada del 2026-08-17 es anterior a la captura de
  fotos. Parece una omisión.

**Regla propuesta.**
1. Si el OCR trae una fecha (`guess_published_date`), manda esa.
2. Si no, y la fuente es WhatsApp, se usa `captured_at.date()`.
3. El operador la puede cambiar siempre.

**Tests primero.**
- Una imagen de WhatsApp sin fecha en el OCR toma `captured_at`.
- Una con fecha estilo Facebook toma la del OCR.
- Una de la bandeja se comporta como hoy.

**Verificación del operador.** Abrir una imagen de WhatsApp pendiente: la fecha de
publicación aparece llena con el día del mensaje.

**`decisions.md`.** Sí.

---

### A3a — Glosario: cargar las convenciones del operador — CERRADO (2026-10-02)

**Objetivo.** Que el modelo deje de abstenerse donde el operador ya sabe la respuesta.
Hoy el glosario tiene 0 entradas en producción.

**Qué entra.** Es una sesión de trabajo conjunto, sin código salvo que aparezca un
bloqueo.
1. Listar candidatos:
   - los vacíos que el operador completó en `llm_reviews` (cantidad y unidad);
   - las notas (`llm_reviews.note`);
   - las convenciones conocidas: «4x10» son 4 paquetes de 10 lb (40 lb); un saco sin
     peso se toma como de 100 lb.
2. El operador decide cada entrada y la carga en `/catalog`, sección Glosario
   (`catalog.py:create_glossary_entry`).

**Verificación del operador.**
- En `/catalog`, las entradas aparecen activas.
- Opcional, cuesta una llamada: abrir un anuncio con «4x10» y pedir «Completar con
  IA». La cantidad tiene que venir llena.

**`decisions.md`.** Sí: la lista de convenciones y el motivo de cada una.

---

### A3b — Glosario: medir su efecto con el backtest — PENDIENTE

**Objetivo.** Saber, con números, cuánto bajan las abstenciones.

**Qué entra.** `python -m saimc.extraction.llm_backtest --execute --limit 100 --seed 42`
con el glosario, comparado contra la corrida anterior guardada. Se miran las
abstenciones de cantidad y unidad y la extracción completa, con el n de cada
porcentaje.

**Gasto.** Entre $0,30 y $0,90, que se aprueba antes. Los últimos backtests costaron
$0,15–0,91 por corrida.

**Verificación del operador.** Leer el resumen en `decisions.md` y el consumo en
`/control`.

**`decisions.md`.** Sí.

---

### A4a — Panel por canal que sirva para decidir — PENDIENTE

**Hechos de partida.** `progress.compute_channel_intake`, que alimenta la sección
«Descarte por canal» de `/control`, tiene cuatro problemas:
- agrupa por nombre, así que dos canales homónimos se funden;
- no desglosa ni por tipo de contenido ni por motivo;
- su tasa incluye los fuera de foco;
- aun así la compara con el 30% de §12.5.

**Qué entra.**
- Agrupar por `channel_id`.
- Desglosar texto e imagen, y el motivo.
- El rendimiento real: filas producidas sobre anuncios resueltos.
- Los minutos humanos por canal (de la bitácora) y las filas por hora humana.
- Corregir la comparación con §12.5.

**Tests primero.** Cálculo puro sobre datos sintéticos.

**Verificación del operador.** En `/control`, el grupo que aporta la mayoría de las
imágenes pendientes aparece con su rendimiento y su costo en minutos.

**`decisions.md`.** Sí: la nueva definición de la tasa.

---

### A4b — Decidir la política de cada canal — PENDIENTE

**Qué entra.** Una sesión de decisión con el panel de A4a. Por canal, una de tres:
- **mantener;**
- **desactivar:** ya existe, `is_active`, en `/catalog`;
- **solo texto:** requiere A4c.

**Cuidado.** Desactivar solo corta lo nuevo. Lo que ya está pendiente de ese canal
sigue en la cola y lo resuelve el lote de A1b.

**Verificación del operador.** En `/catalog`, los canales quedaron según lo decidido.

**`decisions.md`.** Sí: la política de cada canal y su motivo.

---

### A4c — (condicional) Canal «solo texto» — PENDIENTE

Solo si A4b lo elige para algún canal. Toca el esquema, así que lleva una
especificación corta.

**Qué entra.**
- La columna `channels.ingest_images` (booleana, verdadera por defecto).
- `web/routers/whatsapp_webhook.py:whatsapp_webhook` salta las fotos de esos canales.
- Un control para cambiarla en `/catalog`.

**Tests primero.** Una foto de un canal «solo texto» no se ingiere; el texto sí.

---

### A2d — Panel «Horas y automatización» en `/control` — PENDIENTE

**Objetivo.** Las métricas del estudio §9, medidas semana a semana. Es el hito I1.

**Qué entra.**
- **Horas humanas por 1.000 anuncios**, con la duración de la bitácora. El recorte
  de pausas se fija mirando la distribución real y se registra.
- **Tasa de automatización:** el porcentaje resuelto sin tocarlo uno por uno.
- **Frescura:** los días entre `published_date` y el alta de la fila, y la antigüedad
  del pendiente más viejo.
- **Costo de cómputo por anuncio**, desde `llm_proposals`.
- Cada métrica, semana a semana, para ver el antes y el después de A1b.

**Tests primero.** Cálculo puro sobre eventos sintéticos, incluida una pausa larga.

**Verificación del operador.** En `/control`, la sección nueva muestra números
coherentes con lo que hizo esa semana.

**`decisions.md`.** Sí: la definición de cada métrica y el recorte de pausas.

---

### A5a — Motor de validación §6.2 y carriles: especificación — PENDIENTE

**Objetivo.** Especificar una sola vez el motor que usan el modo sombra (A5), la
persistencia (B1) y la automatización (B2). Toca el motor de validación, así que
lleva el flujo completo.

**Hechos de partida.**
- `verify_evidence` y `compare_with_rules` (`extraction/llm_checks.py`) ya existen.
  `compare_with_rules` no compara la provincia.
- `web/price_charts.py:robust_fence` existe (log-IQR×3), pero está declarada «de
  dibujo».
- `cells.py:detect_basis_suspects` consulta la base y no devuelve el id de la fila.
- `validation_status` no existe; `confidence_score` existe y está NULL en todas las
  filas.

**Qué define.** Documento `docs/spec-motor-validacion.md`, o un cambio en
`openspec/changes/`, según se prefiera en la sesión.
- **Reglas de §6.2** que se implementan y cómo:
  - 1 (estructura);
  - 2 (coherencia del precio: la fórmula 70/30 de la spec o una banda robusta de la
    celda, y la decisión se registra);
  - 3 (provincia: la del canal o la del catálogo);
  - 4 (reputación a nivel canal por ahora; por vendedor cuando exista B5);
  - 5 (duplicado: mismo canal, mismo precio y 24 h, además del hash);
  - 6 (coherencia entre cantidad y precio: `detect_basis_suspects` refactorizada para
    evaluar una fila en memoria).
- **Salida por fila:** `validation_status`, `confidence_score` y el detalle de reglas
  (el JSON de «Resultado de validación» de §6.2).
- **Carriles 0, 1 y 2:** los predicados exactos (estudio §4), incluido cómo entra la
  provincia.
- **Señales externas:** aditivas y desconectables. Hay una calibración base sin
  ellas. La especificación fija su contrato, que es el que después implementa A6b:
  - la señal responde solo preguntas cerradas cuyas opciones salen de los catálogos
    del sistema (producto piloto, moneda, provincia, formato de venta);
  - el motor la lee como acuerdo o desacuerdo con el valor extraído, más la
    probabilidad. Un desacuerdo con probabilidad alta manda a revisión; nunca acepta;
  - sin puntaje global de confianza y sin preguntas de coherencia del precio: eso es
    la regla 2 y `verify_evidence`;
  - una respuesta ausente, tardía o de tipo inesperado equivale a «sin señal»;
  - el motor es el único que decide el carril. No hay un orquestador aparte.
- **Protocolo del modo sombra:**
  - calibración sobre el oro del backtest y evaluación sobre las revisiones nuevas,
    sin mezclarlas;
  - métricas: tasa de automatización, error del carril 1 por anuncio y por campo, y
    falsos descartes del carril 0, cada una con cota de Clopper-Pearson;
  - el n mínimo para informar un número.
- **Casos esperados:** de 15 a 25 anuncios sintéticos con el carril y el estado
  esperados, escritos a mano antes del código.
- **Umbrales:** todos «sin fijar» (§12.6). Se miden en A5c.

**Verificación del operador.** Leer y aprobar la especificación.

**`decisions.md`.** Sí, con los apartamientos de la regla 2 y de `robust_fence` si
aplican.

---

### A5b — Motor de validación en código puro, sin esquema — PENDIENTE

**Objetivo.** El motor funcionando sin escribir nada en la base.

**Qué entra.**
- Un módulo nuevo `saimc/validation/`, con las reglas y los carriles.
- Tests escritos desde los casos de la especificación, antes del código.
- `detect_basis_suspects` refactorizada para evaluar filas en memoria, sin romper
  `/control`.
- La banda robusta compartida, si la especificación lo decidió.

**Verificación del operador.**
- Los tests del módulo en verde.
- Nada visible cambia en el sitio, a propósito.

**`decisions.md`.** Solo si la implementación obligó a cambiar algo de la
especificación.

---

### A5c — Informe del modo sombra — PENDIENTE

**Objetivo.** Saber cuánto se podría automatizar y con qué error. Es el hito I2.

**Qué entra.**
- `python -m saimc.validation.shadow`, en seco por defecto, como `measure` y el
  backtest.
- Corre el motor sobre el oro del backtest (calibración) y sobre las revisiones de
  `llm_reviews` (evaluación).
- Los precios de referencia son los de esa fecha: solo cuentan los datos anteriores
  al anuncio.
- Agrega una línea a `docs/shadow-runs.jsonl` y escribe un reporte con el n junto a
  cada porcentaje.
- No gasta API: usa las propuestas guardadas.
- `docs/runbook-rutina-diaria.md` suma una corrida por semana hasta llegar al n.

**Verificación del operador.** Correr el comando en el VPS y leer tres cosas: la tasa
de automatización estimada, el error del carril 1 con su cota y los falsos descartes
del carril 0.

**`decisions.md`.** Sí: el resultado de la primera corrida.

---

### A3c — «Guardar como regla» desde la cola — PENDIENTE

**Objetivo.** Que una convención se capture en el momento en que aparece.
- Hoy el glosario solo se edita en `/catalog`.
- `llm_reviews.note` no se puede promover a regla.

**Qué entra.**
- En el formulario de curaduría, un control para convertir una nota en una entrada
  del glosario (término y definición) sin salir de `/curation`.
- Prototipo a 390 px si la pantalla cambia de forma visible.

**Tests primero.**
- Se crea una `GlossaryEntry` activa.
- La definición pasa por la redacción de datos personales.
- Se rechaza una definición vacía.

**Verificación del operador.** Curar un anuncio, guardar una regla y verla en
`/catalog`.

**`decisions.md`.** Sí.

---

### A1c — (opcional) Lote de textos en foco que el modelo descarta — PENDIENTE

**Objetivo.** Lo mismo que A1b, para los textos donde las reglas ven un producto
piloto pero el modelo dice que hay que descartar (agotado, sin precio). Es alrededor
del 5% del tiempo según el estudio §1.

**Cuándo.** Solo si A2d muestra que vale la pena.

---

### A6 — Prueba de Jev fuera de línea: triage y campos cerrados — PENDIENTE

**Objetivo.** Saber si la probabilidad de Jev está calibrada sobre anuncios cubanos
en español, por pregunta. Sin ese número no hay umbral que fijar.

**Qué entra.**
- Un script fuera del camino crítico (por ejemplo, `scripts/jev_eval.py`) que nada
  del sistema importa.
- Clave propia en `.env` y tope de gasto.
- Solo texto redactado con `redact_personal_data()`.
- **Pregunta de triage:** un sí/no («¿ofrece un producto piloto con precio?»), contra
  las 1.235 decisiones sobre texto (156 curados y 1.079 descartados). Las imágenes
  necesitan K3.
- **Preguntas por campo cerrado** (ampliación del 2026-10-02): producto, moneda,
  provincia y formato de venta, cada una como elección con las opciones de los
  catálogos del sistema. Se comparan contra lo que el operador guardó en los anuncios
  curados. La verdad es la fila curada, nunca la propuesta del extractor.
- **Lo que no se pregunta:** coherencia del precio ni un puntaje global de confianza.
- Las salidas, por pregunta: el diagrama de confiabilidad por tramos, el Brier, el n,
  y el acuerdo con `llm_proposals` (con `discard` para el triage).

**Gasto.** Menos de $0,20, que se aprueba antes. La salida de Jev no se cobra, así
que sumar preguntas casi no lo mueve.

**Paso previo del operador.** Hecho: hay clave (2026-10-02).

**Límite conocido.** Los curados son pocos (156 anuncios). Las preguntas por campo
van a tener cotas anchas; el reporte las muestra con su n y no fija umbrales.

**`decisions.md`.** Sí: Jev entra como señal desconectable por A6b, o se descarta en
firme. Puede ser un resultado distinto por pregunta.

---

### A6b — Jev como señal desconectable del motor, en modo sombra — PENDIENTE

**Depende de:** A6 con resultado favorable, A5b (el motor) y B1 (los campos
persistidos). Si A6 sale desfavorable, pasa a `DESCARTADO`.

Toca el motor de validación, así que lleva el flujo completo. El contrato ya está en
la especificación de A5a («Señales externas»); este slice lo implementa.

**Objetivo.** Que el motor reciba la respuesta de Jev como una señal más, con su
procedencia, y que el modo sombra mida cuánto aporta.

**Qué entra.**
- Un adaptador `saimc/validation/jev_signal.py` detrás de la interfaz de señales
  externas. Apagado por configuración, o sin clave, el motor da exactamente el mismo
  resultado que antes.
- Solo las preguntas que A6 dejó en pie, con las opciones de los catálogos.
- Solo texto redactado con `redact_personal_data()`. Las imágenes son C2.
- Tiempo límite por llamada y tope de gasto por corrida.
- `validation_detail` suma la entrada `jev`: pregunta, respuesta, probabilidad y si
  coincide con el valor extraído.
- A5c informa el modo sombra con Jev y sin Jev, lado a lado.
- La especificación del slice decide dónde corre la llamada (en el pre-cálculo o al
  curar) y si eso pide guardar la respuesta en otra tabla.

**Qué no entra.**
- Cambiar `validation_status`, el carril u `origin` por lo que diga Jev. En sombra
  solo se anota.
- `origin = auto` y el descarte automático: B2a y B2b, después de G1.
- Umbrales fijados a ojo. Salen de A6 y del informe de A5c con Jev, con su n.
- Un segundo extractor de respaldo, o un orquestador fuera del motor.

**Tests primero.**
1. Jev apagado: el motor da lo mismo que sin el adaptador.
2. Jev coincide con probabilidad alta: se anota en `validation_detail`; estado,
   carril y `origin` no cambian.
3. Jev discrepa con probabilidad alta: se anota como desacuerdo; nada más cambia.
4. Jev devuelve un tipo inesperado o una opción fuera del catálogo: se registra el
   evento y la fila queda «sin señal».
5. Jev falla por red, por clave o por tiempo: el motor termina igual.
6. Se alcanza el tope de gasto: se dejan de hacer llamadas y el resto de la corrida
   sigue sin señal.

**Verificación del operador.**
1. `pg_dump`, push, `alembic upgrade head` si hubo migración, reinicio de
   `saimc.service`.
2. Curar un anuncio de texto y abrir su detalle de validación: aparece la entrada
   `jev` con sus preguntas y probabilidades.
3. Apagar Jev en `.env`, reiniciar y curar otro: no aparece la entrada y nada falla.
4. `/control`: el gasto de Jev y cuántas filas tienen señal.
5. **Lo que no tiene que pasar:** una fila con `origin = auto`, un estado distinto
   por culpa de Jev, o una curaduría que falle porque Jev no respondió.

**`decisions.md`.** Sí: Jev entra como señal desconectable del motor, en sombra, con
las preguntas que quedaron. Los umbrales se registran en G1, no acá.

---

## Colaterales — sin compuerta, durante la acumulación

### K1 — `/edit` con historial — PENDIENTE

**Hecho.** `submit_edit` actualiza `prices_history` en el lugar, sin rastro. Eso va
contra el espíritu de §6.5 y deja cambiar el oro sin que se note.

**Qué entra.** Un evento `edited` en la bitácora, con los valores de antes y de
después en `detail`, y la vista del historial en `/curation/history`.

**Tests primero.** Al editar queda el antes y el después; al borrar una fila en la
edición, también.

### K3 — Guardar la transcripción del OCR — PENDIENTE

**Hechos.**
- Hoy el texto del OCR solo queda en `raw_ingestions.original_text`, y solo al curar.
- Cada imagen se transcribe dos veces: una en el pre-cálculo y otra al abrirla.
- Las imágenes descartadas nunca lo guardan.
- `ocr._transcribe_rapidocr` descarta las cajas que RapidOCR sí devuelve.

**Qué entra.** Una tabla derivada, de solo altas, con el texto, el motor y (para C2)
las cajas. Toca el esquema, así que lleva una especificación corta.

**Por qué importa.** Acelera la cola y hace posibles A6 con imágenes, la auditoría de
imágenes y C2.

### K4 — Aviso de hueco de ingesta por canal — PENDIENTE

**Hecho.** Entre el 2026-09-08 y el 2026-09-20 no entró nada, y nadie se enteró a
tiempo.
- `health_strip.intake_indicator` es global y se pone ámbar recién a los 2 días.
- Solo se ve si se abre `/control`.

**Qué entra.**
- Un aviso por canal activo sin mensajes en N horas, en `/curation` y en `/control`.
- N se fija con los huecos típicos de cada canal, medidos.
- La entrada en `decisions.md` sobre el hueco de septiembre, que no está registrado.

### K6 — Ensanchar `llm_proposals.prompt_version` — PENDIENTE

**Hecho.** La versión con glosario ocupa 30 de los 32 caracteres. Si
`PROMPT_VERSION` crece, se trunca.

**Qué entra.** Pasarla a 64 caracteres, en la próxima migración que se haga por otro
motivo.

---

## Fase B — lo que la spec ya pide, y la automatización

B1 y B5 implementan lo que la spec ya pide, así que pueden hacerse antes de la
compuerta. B2, B3 y B4 solo después de G1.

### B1 — Persistir la validación: estado, puntaje y procedencia — PENDIENTE

Toca el esquema, así que lleva el flujo completo, apoyado en la especificación de
A5a.

**Qué entra.**
- **Migración:** `prices_history.validation_status`, `validation_detail` (JSON) y
  `origin` (procedencia: `human|auto|audited`). `confidence_score` ya existe y pasa a
  llenarse.
- **Filas existentes:** la especificación decide si se completa `origin = 'human'`.
  Se sabe con certeza.
- **Cuándo corre:** el motor corre al curar y al editar, y guarda su resultado.
- **Dónde se ve:** `/control` muestra la distribución de estados.

**Tests primero.**
- Al curar se guardan estado, puntaje y detalle.
- Al editar se recalculan.
- Un precio fuera de la banda queda como anomalía.

**Verificación del operador.** Curar un anuncio con un precio normal (queda validado)
y uno absurdo (queda como anomalía, visible en `/control`). El operador sigue
decidiendo igual que antes.

### B5 — Emisor de grupo como hash con clave — PENDIENTE

**Hecho.** El `participant` de los grupos se descarta
(`ingestion/whatsapp_backfill.py`). Guardarlo en claro violaría §10.6, porque el JID
suele ser el número de teléfono.

**Qué entra.**
- Una columna escrita una sola vez al ingerir: HMAC-SHA256 del JID, con una clave en
  `.env`.
- Nunca se guarda ni se registra en logs el JID, y el hash nunca se expone.
- Sin clave configurada queda NULL y el sistema sigue igual.
- Habilita dos cosas: la deduplicación «mismo vendedor, mismo valor, 24 h» (como
  elTOQUE) y la regla 4 por vendedor.

**Tests primero.**
- El mismo JID da el mismo hash.
- Otra clave da otro hash.
- El JID no aparece en ninguna columna ni log.
- Sin clave, NULL.

**`decisions.md`.** Sí, incluido que rotar la clave corta la continuidad del
historial.

### G1 — Compuerta: ¿se automatiza? — PENDIENTE

**Qué se lleva.** A2d (horas antes y después), A5c con el n de la especificación y,
si hubo, A6 y el informe de sombra con Jev de A6b. Si Jev entra en algún carril, sus
umbrales se fijan acá con su n.

**Qué decide el operador.**
1. ¿Se enciende el carril 0 (descarte automático)? El riesgo es perder datos, no
   corromperlos.
2. ¿Se enciende el carril 1 (aceptación automática)? ¿Con qué meta de error? La meta
   la elige el operador; el umbral lo da el dato.
3. ¿De qué tamaño es la auditoría semanal?

**Qué se escribe si la respuesta es sí.**
- Las entradas en `decisions.md` que reemplazan en parte a la decisión cerrada 2.
- Los apartamientos de §6.1 y §10.7 en la tabla «Apartamientos deliberados» de
  `docs/spec-map.md`.
- El texto nuevo de la decisión 2 en `CLAUDE.md`, que el operador edita o aprueba.

**Criterio mínimo sugerido.** La cota superior al 95% del error del carril 1 queda
por debajo de la meta, con el n de la especificación. Si no se llega, se sigue
acumulando o se estrecha el carril.

### B2a — Carril 0 encendido: descarte automático con auditoría — PENDIENTE

**Qué entra.**
- Con `SAIMC_LANE0_ENABLED` (apagado por defecto), el pre-cálculo aplica el carril 0
  al terminar cada propuesta, y el evento queda con `mode = auto`.
- `/curation/auditoria` muestra una muestra aleatoria semanal de los descartes
  automáticos, reproducible con una semilla por semana.
- Rescatar un anuncio lo devuelve a `PENDING` y deja un evento.

**Tests primero.**
- Con el flag apagado no cambia nada.
- Encendido, descarta solo lo que el motor manda.
- La muestra es reproducible.
- El rescate funciona.

**Verificación del operador.**
1. Pre-cálculo con el flag encendido.
2. `/control` muestra cuántos se descartaron solos.
3. `/curation/auditoria` muestra la muestra.

### B2b — Carril 1 encendido: aceptación automática con auditoría — PENDIENTE

**Qué entra.**
- Con su propio flag, el pre-cálculo guarda las filas de los anuncios del carril 1
  con `origin = auto`. Resuelve la tasa de cambio igual que `submit_curation`, para
  lo que se refactoriza el guardado y se comparte.
- **Auditoría semanal:** aleatoria y estratificada por canal. Lo auditado pasa a
  `audited` o se corrige, con historial (K1).
- **Umbrales:** se leen de una configuración documentada en `decisions.md`.

**Tests primero.**
- Un anuncio del carril 1 produce exactamente las mismas filas y la misma tasa que si
  lo curara el operador.
- Con el flag apagado no pasa nada.

### B2c — Freno automático del carril 1 — PENDIENTE

**Qué entra.**
- Si la cota superior del error auditado supera la meta, el carril 1 se apaga solo.
- `/control` lo muestra en rojo con el motivo.
- Volver a encenderlo es una decisión del operador.

**Tests primero.** Una secuencia de auditorías con errores apaga el carril.

### B3 — Conjunto de referencia congelado y métrica de Fase 0 redefinida — PENDIENTE

**Hechos.**
- La marca de conjunto de referencia que pide §6.1 no existe.
- El oro se recalcula en cada corrida, y `/edit` puede cambiarlo.

**Qué entra.**
- **Congelar los curados a mano:** una lista de ids con el hash de sus valores. Toca
  el esquema, así que lleva una especificación corta.
- **`measure.py`:** la métrica de la Fase 0 pasa a ser la tasa de automatización más
  el error auditado. H0 queda como medición del extractor sobre el conjunto
  congelado.
- **Registro:** el apartamiento de §12.5 y §3 queda en `spec-map.md` y
  `decisions.md`.

### B4 — Capa pública: por qué confiamos en cada dato (§6.5) — PENDIENTE

**Qué entra.**
- En el tablero y en Explorar, por celda: el n, la proporción humano, automático y
  auditado, y el estado de validación.
- «Sobre los datos» actualizado.
- Prototipo a 390 px aprobado antes de programar.

---

## Fase C — escala

- **C1 — Temporizador del pre-cálculo.** Un temporizador de systemd corre `prefill
  --execute --max-cost X`, con los carriles si están encendidos, con log y aviso si
  falla. Se registra el cambio de motivo de la decisión cerrada 3. La API por lotes
  (−50%) se suma cuando el volumen lo justifique.
- **C2 — Imágenes con modelo de visión.** Tres pasos:
  - C2a: cajas del OCR (K3) y máscara de teléfonos y nombres, con tests sobre
    imágenes sintéticas;
  - C2b: backtest de imágenes, visión contra OCR más texto, sobre el oro (hoy 55,6%);
    cuesta alrededor de $1 cada 100 imágenes y se aprueba antes;
  - C2c: el apartamiento de privacidad registrado, y la integración desconectable.
- **C3 — Controles de §10.7.** C1 a C4, antes de sumar fuentes nuevas o abiertas. El
  N mínimo por celda se mide (§12.6).
- **C4 — Expansión.** Provincias (§9.0: tiene que ser configuración, no desarrollo),
  productos y canales, de a una tanda por vez y mirando las horas por 1.000 anuncios
  y el error auditado.
- **Modelos propios.** Solo si el costo de la API o la dependencia del proveedor lo
  justifican (estudio §8). Sin disparador, no se abre.

---

## Costo de API previsto

| Qué | Cuánto | Cuándo |
| --- | --- | --- |
| Rutina diaria de pre-cálculo | $0,0015–0,0029 por anuncio, alrededor de $1 por semana al volumen actual (estimado) | siempre |
| A3b, backtest con glosario | $0,30–0,90 | una vez |
| A6, prueba de Jev | menos de $0,20 | una vez |
| A6b, Jev en sombra | del orden de $0,0001 por anuncio (estimado), con tope por corrida | mientras esté encendido |
| C2b, backtest de imágenes con visión | alrededor de $1 cada 100 imágenes | una vez |

Todo gasto se aprueba antes con el monto estimado.

## Fuera de alcance

- Modelos propios, afinado, destilación y Snorkel (estudio §8).
- Un segundo LLM como verificador.
- Redis y Celery.
- Mandar imágenes sin redactar a un proveedor.
- Publicar solo en redes.

## Riesgos del plan

| Riesgo | Qué se hace |
| --- | --- |
| El modo sombra no llega al n porque se cura poco | La rutina diaria sigue. El oro sirve de calibración desde el día uno. Si en seis semanas no se llega, se revisa en G1 con lo que haya y la cota que dé |
| Las migraciones en producción | `pg_dump` antes de cada `alembic upgrade head`, en los pasos de verificación |
| El sello automático aumenta con los lotes | A2c corregida, «Rescatar» visible y, después de G1, la auditoría |
| El plan crece | Lo nuevo va a «Pendientes sueltos». Los cambios de orden se anotan en la línea «Última actualización» |
