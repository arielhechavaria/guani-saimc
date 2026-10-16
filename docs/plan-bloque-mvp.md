# Plan de trabajo — último bloque antes del lanzamiento del MVP

Parte de `docs/reporte-estado-2026-10-16.md` (léelo primero: cada afirmación
técnica de acá se apoya en él). Es una **propuesta**: nada de esto está
aprobado ni empezado. Las tareas T1–T10 son la lista del operador; T11–T16
son las del primer mensaje (T11 son los cuatro frenos del carril 1).

Reglas del plan: un slice por sesión, cada uno en su worktree, el push lo
hace el operador, y al cerrar cada slice se entregan pasos de verificación
(`CLAUDE.md`).

## 1. Orden recomendado

La complejidad es esfuerzo estimado: **S** (≤ medio día), **M** (1–2 días),
**L** (más de 2 días o con aprobación intermedia).

| Ola | Tarea | Qué | Cmplj. | Modelo | Por qué en este lugar |
|---|---|---|---|---|---|
| 0 | — | **Acción del operador:** restituir `SAIMC_EL_TOQUE_API` en el `.env` del VPS y reiniciar | S | — | Corta la hemorragia: mientras falte, cada fila nueva se convierte con una tasa de relleno. |
| 0 | — | Resolver `D .venv` (commit de baja) | S | haiku | Aprobado por el operador. Deja el repo limpio para abrir slices. |
| 0 | T1 | Saneamiento de filas con tasa de relleno | M | opus | **Subió a primera.** La tasa del USD es volátil: el período afectado está mal. Ensayo sobre un volcado restaurado en una base temporal, sin esperar a T2. |
| 0 | T0 | Definir «fila validada» y medir cuántas hay | M | opus | Antes de decidir la transición de fase y la corrida oficial de H0. Toca el motor: flujo completo. |
| 1 | T3 | Jev deja el modo sombra | M | opus | Decidido por el operador; el análisis y el alcance están en `docs/analisis-jev-decisor.md`. Toca el motor: flujo completo. |
| 1 | T2 | Entorno de desarrollo local (Docker) | M | sonnet | Restricción levantada (`decisions.md`, 2026-10-16). |
| 1 | T9 | Revisión de auditorías | M | sonnet | La muestra semanal es el lunes 19/10; el freno depende de ella. |
| 1 | T13 | Versión de gentle-ai en el VPS y «ODD» | S | sonnet | Investigación de solo lectura; condiciona cómo se desarrolla el resto. |
| 2 | T5 | Explorar: precio real del formato | S | sonnet | Cambio acotado de presentación. |
| 2 | T6 | Filtros globales + bug de provincia | M | sonnet | Cimiento para T7 y T16. |
| 2 | T7 | Rangos 3 / 7 / 90 días | S | sonnet | Depende de T6. |
| 3 | T11a | Vista de desacuerdo modelo/reglas | M | sonnet | El freno más grande (79 anuncios) y no toca el motor. |
| 3 | T11b | Tope de filas por anuncio, medido | M | opus | Parámetro de riesgo del motor de validación. |
| 3 | T11c | Canal sucio: pasa si modelo y reglas coinciden | S | opus | Una línea, pero cambia una decisión de G1b. |
| 3 | T11d | Regla de «otra base» | L | opus | Análisis antes de cambiar nada; mejor tras T11a. |
| 4 | T4 | Rediseño visual de `/control` | L | opus (prototipo) + sonnet (build) | Dos pasos con aprobación del operador en medio. |
| 5 | T10 | SEO puntual + analítica | M | sonnet | Antes de publicar en redes: el tráfico necesita llegar a algo indexable. |
| 5 | T8a | Tarjetas diarias a disco, 6:00 hora de Cuba | M | sonnet | Genera material sin depender de Meta. |
| 5 | T16 | Tour de primera visita | M | sonnet | Último de la cadena de interfaz: los pasos se anclan a filtros ya estables. |
| 5 | T15 | Guía de crecimiento en `docs/` | S | opus | Documento de estrategia; puede ir en paralelo. |
| 6 | T8b | Publicación automática en Instagram (API) | L | opus | Decidido por el operador; el descarte del 2026-09-28 quedó revocado. Primero se verifica lo que exige Meta, luego se construye. |
| 6 | T14 | Análisis de fase y transición | M | opus | Depende de T0 y T1. Solo si hay capacidad de validar filas se corre H0. |
| 6 | T12 | ¿Paper del sistema? | S | opus | Después de tener cifras de H0 y de auditoría estables. |

Decisiones del operador del 2026-10-16 incorporadas (sección 3). La
transición de fase se decide cuando exista la capacidad de validar una fila
(T0) y, solo entonces, con la corrida de H0 (T14).

## 2. Prefijo común de los prompts

Pegar al inicio de cada prompt:

> Proyecto SAIMC/Guani. Lee `CLAUDE.md`. Respondé en español; código en
> inglés. Antes de proponer algo de arquitectura, esquema o métricas, abrí
> `docs/spec-map.md` y leé el rango citado; citá por sección, no por línea.
> Trabajá en un worktree propio (`git worktree add .claude/worktrees/<slice>
> -b worktree-<slice>`); nunca en `master`. Un solo slice. Commits
> convencionales, sin atribución de IA. No hagas push. Si hay cambios sin
> commitear que no continúan esto, parás y avisás. Al cerrar: tests en verde,
> `docs/checklist-pr.md`, entrada en `docs/decisions.md` si la decisión no es
> obvia, y pasos concretos para verificar (URL, qué mirar, qué debe y qué no
> debe pasar). Si el slice deja un comando nuevo, va a `docs/comandos.md` en
> el mismo commit.

## 3. Decisiones que necesito del operador

Respuestas del operador del 2026-10-16 y su estado:

| # | Decisión | Estado |
|---|---|---|
| 1 | Docker permitido para desarrollo (T2) | **Registrada** en `decisions.md` (2026-10-16). Se anotó también en `CLAUDE.md`, decisión cerrada 4. |
| 2 | Publicar en Instagram por API (T8b) | **Registrada** en `decisions.md` (2026-10-16), con lo que implica. Reemplaza el punto (1) del «Descartado» del 2026-09-28. |
| 3 | Jev deja el modo sombra (T3) | Argumentada en `docs/analisis-jev-decisor.md`. Confirmado: sale de la sombra, confirma pero no acepta solo, y la provincia entra a la medición. **Se registra en `decisions.md` al cerrar la medición de T3**, con flujo completo. |
| 4 | Qué es una fila «validada» (T0) | **Sin decidir.** Hay un mecanismo (`approved`) pero los datos sugieren que ninguna fila lo alcanza. T0 lo mide y propone la definición. |
| 5 | Transición de fase y corrida de H0 (T14) | **Criterio fijado por el operador:** se decide cuando haya capacidad de validar filas; si las condiciones son correctas, entonces se corre H0. |
| 6 | `D .venv` | **Aprobado** resolverlo (tarea corta de la ola 0). |

### Lo que sigue pendiente de decidir por el operador

Ordenado por cuándo estorba. Ninguna de estas bloquea la ola 0.

| # | Decisión | Para qué tarea | Propuesta de partida |
|---|---|---|---|
| 1 | Qué hacer con filas cuya fecha no tiene tasa en elTOQUE: dejarlas marcadas o excluirlas de los agregados públicos | T1 | Dejarlas marcadas y excluirlas del público hasta tener tasa |
| 2 | Definición de «fila validada» | T0 | `approved` con procedencia `human` o `audited`; una fila `auto` sin auditar queda `unverified` |
| 3 | Umbral de descarte de Jev: P(sí) < 0,1 o < 0,2 | T3 | 0,1 (0 falsos en 109) |
| 4 | ¿Jev descarta por su cuenta cuando el asistente no pudo proponer? | T3 | Sí, con el mismo umbral |
| 5 | Nuevo rango por defecto al pasar a 3 / 7 / 90 días | T7 | 7 días |
| 6 | Cuál fecha es la correcta para `SAIMC_LANE1_AUDIT_SINCE`: 10-14 (decisión) o 10-15 (`.env`) | T9 | Confirmar y alinear |
| 7 | Qué muestra la vista de desacuerdo como «lectura de las reglas»: las reglas solo detectan el producto, no proponen filas | T11a | Mostrar producto y precio detectados |
| 8 | Si el precio del envase se muestra solo en Explorar o también en el tablero | T5 | Solo en Explorar |
| 9 | Comprobar en producción: valor de `SAIMC_SITE_GATE_ENABLED` y qué hay delante de uvicorn (Caddy) | T10 | — (dato, no decisión) |
| 10 | Qué es «ODD» (¿lo mismo que RDD?) | T13 | — (dato que aporta el operador) |
| 11 | Confirmar el orden de olas de la sección 1 | todas | Es una propuesta: no hubo respuesta explícita |

Acciones del operador que no son decisiones: restituir `SAIMC_EL_TOQUE_API` en
el `.env` del VPS (T1), hacer push de las 6 ramas sin integrar (reporte §3) y,
en el VPS, `git pull` y reinicio para ver esta página.

## 4. Las tareas

### T0 — Definir «fila validada» y medir cuántas hay · M · opus

**Hallazgo (corrige al reporte §1):** sí existe un mecanismo. El motor asigna
siete estados (`validation/types.py:33-43`) y **`approved` es el único que
significa «validado»**: las seis reglas en `pass` **y** la fila verificada
(`validation/engine.py:64-84`; `spec-motor-validacion.md:122-131`). Pero
«verificada» es `provenance == human` y nada más (`persist.py:87`), de modo
que una fila del carril 1 (`auto`) nunca llega a `approved`, ni siquiera
después de auditada (`audited`) (inferido del código; no ejecutado). Los
conteos del reporte suman 1101 sin ninguna fila `approved`: **no verificado**
(hay que confirmarlo con un SELECT). Si es así, hay 461 filas humanas que
deberían poder ser `approved` y no lo son: o falla alguna de las seis
reglas, o hay un defecto. Eso se investiga primero.
**Jev no resuelve esto:** una señal de Jev no cambia `provenance` ni crea
`approved` (el motor no la lee). Ver `docs/analisis-jev-decisor.md`.
**Alcance:** (1) medir con SELECT cuántas filas hay por `validation_status` y
`provenance`, y por qué las humanas no son `approved`; (2) proponer la
definición de «validada» (propuesta de partida, a discutir: `approved` con
`provenance` en `human` o `audited`; una fila `auto` sin auditar queda
`unverified`); (3) con la aprobación del operador, flujo completo
(explorar → especificar → diseñar) porque toca el motor.

> **Prompt.** [prefijo] Slice `definir-validada`, solo lectura primero. Con
> SELECT sobre `prices_history`, contá filas por `validation_status` y
> `provenance`, y explicá por qué las filas humanas no llegan a `approved`
> (`validation/engine.py` `_status`, `persist.py` `verified`). Leé
> `spec-motor-validacion.md` y `spec.md` §6.2. Proponé una definición de
> «fila validada» y cómo debe validarse (incluyendo filas `auto` auditadas),
> con el efecto en `trust_view.py` y en los números públicos. No cambies el
> motor sin aprobación; si se cambia, flujo de especificación completo y
> entrada en `decisions.md`.


### T2 — Entorno de desarrollo local con Docker · M · sonnet

**Estado:** no hay Dockerfile ni compose; el Postgres local es nativo; el
`.env` apunta a producción (`backfill.py:5-11`).
**Decisión previa:** la restricción de Docker quedó levantada el 2026-10-16
(`decisions.md`). Esto no obliga a containerizar la app en producción.
**Alcance:** un `docker-compose.dev.yml` con PostgreSQL como mínimo (si
conviene, también la app con su venv montado: elegir lo más simple que
funcione), un `env.dev.example` que apunta a esa base, y un comando que
levanta la base, aplica `alembic upgrade head` y carga una copia **sin datos
personales** de producción (o datos sintéticos). Fuera de alcance: cambiar
cómo corre la app en el VPS.
**Verificación:** `docker compose -f docker-compose.dev.yml up -d`, correr la
suite, abrir `127.0.0.1:8000`, y comprobar que `SELECT current_database()`
no es la de producción.

> **Prompt.** [prefijo] Slice `dev-docker`. Creá un entorno de desarrollo
> local con Docker Compose que levante únicamente PostgreSQL (misma versión
> que producción: verificala en el VPS o en `docs/`), con volumen persistente
> y puerto distinto del nativo. Agregá `env.dev.example`, un script que haga
> `alembic upgrade head` y siembre datos, y la sección correspondiente del
> README y de `docs/comandos.md`. La restricción de Docker quedó levantada
> (`docs/decisions.md`, 2026-10-16): no hace falta otra entrada, pero cualquier
> cambio en cómo corre la app en producción sí necesita la suya. El `.env` de
> desarrollo nunca debe apuntar a producción: agregá una guarda en el
> arranque que se niegue a correr comandos destructivos si la URL de la base
> es la de producción.

### T13 — gentle-ai en el VPS y «ODD» · S · sonnet

**Estado:** no verificado en el VPS. Dato local: la configuración que carga
esta sesión llama a la metodología **RDD** (*receipt-driven development*),
con un interruptor (`gentle-ai review mode enable|disable|status`). Si «ODD»
es otro nombre o una versión nueva, hay que confirmarlo contra la
documentación oficial.
**Alcance:** investigación y argumentación, sin actualizar nada.
**Entregable:** `docs/investigacion-gentle-ai.md` con versión instalada,
última versión publicada, qué cambia, qué habría que modificar en `CLAUDE.md`
y en los flujos, riesgo de actualizar y recomendación.

> **Prompt.** [prefijo] Slice `investigar-gentle-ai`, solo lectura. En el VPS
> (por SSH, solo comandos de lectura) averiguá la versión instalada de
> gentle-ai (`gentle-ai --version`), y compará con la última publicada en el
> repositorio oficial y sus notas de versión. Aclará qué es «ODD» y si es lo
> mismo que RDD. Listá qué habría que cambiar en `CLAUDE.md`, en los skills y
> en el flujo de slices si se actualiza, y qué se rompe si no. No actualices
> nada. Entregá `docs/investigacion-gentle-ai.md` con la recomendación
> argumentada y marcá «no verificado» lo que no puedas comprobar.

### Resolver `D .venv` · S · haiku

El índice tiene `.venv` como symlink (modo 120000) y el working tree lo borró.
Elegir: commitear la baja (`git rm --cached .venv`) y confirmar que
`.gitignore` ignora `.venv` sin barra final, o restaurarlo. Sin esto,
cada sesión recibe la advertencia de «slice abierto».

> **Prompt.** [prefijo] Slice `limpiar-venv-symlink`. `git status` muestra
> `D .venv`: el índice tiene un symlink (modo 120000) que se commiteó por
> error el 2026-10-13. Verificá que `.gitignore` ignora `.venv` y `.venv/`,
> hacé `git rm --cached .venv` y commiteá. No toques el venv real.

### T9 — Revisión de las auditorías · M · sonnet

**Estado (reporte §7):** el error posterior al reinicio no tiene cifra; hay
una discrepancia de fecha (`.env` 10-15 vs decisión 10-14); dos casos «No era
un anuncio válido» sin causa; y Telegram no avisa cuando falta auditar.
**Alcance:** revisión de solo lectura primero; las correcciones, si las hay,
en un slice aparte.
**Entregable:** `docs/revision-auditorias.md`.

> **Prompt.** [prefijo] Slice `revisar-auditorias`, solo lectura primero.
> Revisá carril 0 y carril 1 (`validation/lane0.py`, `lane1.py`,
> `automation.py`, `/curation/auditoria`). Calculá con SELECT el error
> auditado posterior al reinicio de la medición; resolvé la discrepancia
> entre `SAIMC_LANE1_AUDIT_SINCE` (`.env`) y `decisions.md` (2026-10-14);
> investigá la causa común de los dos «No era un anuncio válido» (ids
> 8cb31516 y 230a3abb); y evaluá avisar por Telegram cuando haya aceptados
> sin auditar (riesgo en `decisions.md`). Entregá `docs/revision-auditorias.md`
> con hallazgos, cifras y cambios propuestos.

### T1 — Saneamiento de filas con tasa de relleno · M · opus

**Estado (reporte §5):** sin `SAIMC_EL_TOQUE_API` el sistema usó
`cache.latest()`. Las 264 filas son **no verificado**.
**Por qué es lo primero:** el dólar es volátil. Una fila convertida con la
tasa de otro día tiene un `price_cup` equivocado, y todo lo que se calcula
encima (celdas, rangos, tablero público, H0) arrastra ese error durante el
período afectado. Hasta sanearlo, los números de ese período no son
confiables.
**Orden:** (1) el operador restituye la variable en el `.env` del VPS y
reinicia, para que no entren filas nuevas con relleno; (2) respaldo
(`saimc-backup` o un `pg_dump` manual); (3) **ensayo sobre ese volcado
restaurado en una base temporal** (no hace falta esperar a T2); (4) dry-run en
producción; (5) `--apply`.
**Duda a resolver en el dry-run:** si la tasa de un día no existe en elTOQUE,
la fila queda sin sanear. Hay que decidir con el operador qué hacer con ellas
(dejarlas marcadas, o excluirlas de los agregados públicos).
**Alcance:** ampliar el backfill (`exchange_rate/backfill.py:68-71`) para
incluir `exchange_rate_source = 'elTOQUE (cache)'`, consultar la tasa de la
fecha ancla de cada fila y reescribir con `apply_resolution`
(`service.py:88-104`), el único escritor de las cuatro columnas. Revisar y
corregir las filas de relleno en `exchange_rate_cache`. Si elTOQUE no tiene
dato de un día, la fila **no se toca** y se reporta (no cae otra vez al
relleno). Fuera de alcance: cambiar el esquema. Pregunta aparte, para otro
slice: ¿`cache.latest()` debería fallar cerrado en vez de rellenar?
**Verificación:** dry-run con tabla antes/después por fecha; tras `--apply`,
el `SELECT ... GROUP BY exchange_rate_source` ya no muestra filas de relleno
salvo las sin dato; los dos CHECK de `prices_history` se cumplen;
`raw_ingestions` intacto.

> **Prompt.** [prefijo] Slice `sanear-tasa-cache`. Hay filas de
> `prices_history` con `exchange_rate_source = 'elTOQUE (cache)'` porque
> `SAIMC_EL_TOQUE_API` faltó del `.env`. Primero, con SELECT, contá y
> describí esas filas (cuántas, qué fechas, qué monedas) y revisá
> `exchange_rate_cache` por filas de relleno. Después ampliá
> `exchange_rate/backfill.py` para recalcularlas: para cada fila usá la fecha
> ancla (`anchor_date`), consultá elTOQUE para ese día y reescribí con
> `apply_resolution`. Si no hay tasa para ese día, no la modifiques y
> reportala. Dry-run por defecto, `--apply` explícito, pausa entre
> consultas, respaldo previo. `raw_ingestions` no se toca. Tests primero:
> definí los casos esperados (con tasa del día, sin tasa del día, moneda CUP,
> CHECKs de la tabla) antes de escribir el código. Documentá el comando en
> `docs/comandos.md`.

### T3 — Jev deja el modo sombra · M · opus

**Decidido por el operador** (2026-10-16). Pros, contras y la recomendación de
alcance están en `docs/analisis-jev-decisor.md`. Resumen: los datos respaldan
a Jev para **descartar**; para aceptar solo sirve como **condición adicional**
(coincidir con el asistente en producto, moneda y provincia), nunca como quien
acepta, porque no opina sobre precio, cantidad ni unidad. Como decisor no crea
filas «validadas» (eso es T0). **Pendiente del operador:** el umbral del
descarte (0,1 o 0,2) y si Jev descarta solo cuando el asistente no pudo
proponer. Toca el motor de validación: flujo completo.
**Antes de programar, medir** (solo lectura): cuántas huellas
(`questions_hash`) distintas hay entre las señales guardadas y cuántas son
vigentes; el costo mensual real (`SUM(cost_usd)`); el acuerdo con la
curaduría humana sobre las señales actuales (`saimc.validation.shadow`); y,
**incluyendo la provincia** como pregunta nueva, cuántos errores del carril 1
habría frenado Jev como condición y cuántas filas buenas habría frenado de más.

> **Prompt.** [prefijo] Slice `jev-decisor`, flujo de especificación completo
> (toca el motor). Leé `docs/analisis-jev-decisor.md` y confirmá con el
> operador el alcance antes de seguir. Primero, solo lectura: con SELECT
> sobre `jev_signals` agrupá por `questions_hash`, `model` y `source`;
> sumá `cost_usd`; y corré `saimc.validation.shadow` contra la base real para
> medir el acuerdo de Jev con la curaduría humana. Agregá la pregunta de
> provincia (catálogo cerrado; cambia `questions_hash`) y medí si Jev como
> condición de aceptación (coincidir con el asistente en producto, moneda y
> provincia) habría frenado errores reales del carril 1 y a cuántas filas
> buenas. No lo enciendas si la medición no lo respalda. Solo después especificá
> cómo entra Jev al motor (`validation/engine.py` `ExternalSignal`,
> `lane1.CONFIG.signal_high_probability`, carril 0), con tests escritos antes
> con casos a mano, y la entrada de `docs/decisions.md` que reemplaza lo
> dicho en 2026-10-05 («Jev no entra»).

### T5 — Explorar: precio real del formato · S · sonnet

**Estado (reporte §6):** el formato «paquete 1 kg» muestra el precio por lb.
**Alcance:** en el desglose por formato, mostrar el precio del envase
(precio por lb × lb del formato: 1 kg = 2,2046 lb) y, secundario, el precio
por lb. Usar `units.convert_quantity`. Decidir con el operador si se muestra
solo en Explorar.
**Verificación:** `/precios?vista=explorar` con un producto que tenga formato
paquete 1 kg: el valor mostrado = precio/lb × 2,2046 (redondeo a 2
decimales), y la fila «sin formato» sigue en precio por lb.

> **Prompt.** [prefijo] Slice `explorar-precio-formato`. Leé
> `docs/DESIGN.md` antes de tocar plantillas. En `web/explore_view.py`
> (`_by_format`, `_display`) el desglose por formato etiqueta el grupo pero
> muestra precio por unidad canónica (lb). Mostrá el precio del envase
> (precio por lb × cantidad del formato, con `units.convert_quantity`) y el
> precio por lb como dato secundario. Tests primero con casos escritos a mano
> (1 kg = 2,2046 lb; caja de 40 lb; sin formato). Mobile first, 390 px.

### T6 — Filtros globales y bug de provincia · M · sonnet

**Estado (reporte §6):** varios constructores de URL, parámetros perdidos
entre páginas, y el bug del tablero sin reproducir.
**Alcance:** (a) reproducir el bug con
`/precios?provincia=santiago-de-cuba&desde=2026-10-08&hasta=2026-10-15&vista=tablero`
y escribir el test que lo captura; (b) un `FilterState` único en
`product_nav.py` (productos, pestaña, provincia, rango, ver, capa, vista) con
`.with_(...)` y `.url(path)`, usado por todas las plantillas; (c) que los
filtros viajen a `/producto/<slug>` y de vuelta.
**Verificación:** desde la lista, aplicar fecha + provincia, entrar a un
producto y ver el mismo filtro; volver y que se conserve; el botón «Fechas»
no pierde pestaña ni capa.

> **Prompt.** [prefijo] Slice `filtros-globales`. Leé `docs/DESIGN.md`.
> Primero reproducí con un test el bug de
> `/precios?provincia=santiago-de-cuba&...&vista=tablero` (hipótesis a
> verificar, no a dar por cierta: `watchlist_view.py` lista productos sin
> celda en la provincia enfocada y ordena por el total de todas las
> provincias). Después centralizá el estado de filtros en un dataclass
> `FilterState` en `web/product_nav.py` y reemplazá los constructores de URL
> sueltos (`build_window_nav`, `build_province_nav`, `build_layer_nav`,
> `period_hidden`, `map_href`, `_product_link`). El filtro de provincia y el
> de fecha deben llegar a `/producto/<slug>` y volver. Tests primero.

### T7 — Rangos 3 / 7 / 90 días · S · sonnet

**Estado:** `WINDOW_CHOICES = (7, 30, 90)` y `DEFAULT_WINDOW_DAYS = 30`
(`product_nav.py:25,30`); el valor por defecto alimenta descargas, `/control`
y la landing, así que **hay que elegir un nuevo valor por defecto** (propongo
7 días; decide el operador). Un `?dias=30` guardado en enlaces viejos caería
al valor por defecto: aceptable, pero verificarlo.
**Verificación:** el selector muestra 3 / 7 / 90 y «Fechas»; un rango
distinto enciende «Fechas»; la landing y `/control` siguen mostrando datos.

> **Prompt.** [prefijo] Slice `rangos-3-7-90`, después de `filtros-globales`.
> Cambiá `WINDOW_CHOICES` a `(3, 7, 90)` y elegí con el operador el nuevo
> `DEFAULT_WINDOW_DAYS`. Revisá todos sus usos (`_product_link`,
> `DOWNLOAD_WINDOW_DAYS`, `routers/control.py`, landing, textos fijos
> «7 / 30 / 90» en plantillas, metodología). Actualizá los tests que fijan
> 7/30/90.

### T11a — Vista de desacuerdo modelo/reglas · M · sonnet

**Estado:** `engine.py:464`; 79 anuncios. Las reglas solo detectan el
producto: no proponen filas.
**Decisión previa (corta):** la vista muestra «el modelo dice: descartar» y
«las reglas ven: producto X, precio Y» (lo detectado), con tres acciones:
aceptar la lectura del modelo, aceptar la de las reglas (abre el formulario
de curaduría con el producto detectado) o descartar.
**Verificación:** un anuncio de la lista de desacuerdo muestra ambas lecturas
y cada botón deja el anuncio en el estado correcto.

> **Prompt.** [prefijo] Slice `vista-desacuerdo`. Los anuncios que el modelo
> descarta pero las reglas ven con producto piloto (`engine.py:464`) hoy van a
> la cola general. Creá una vista de curaduría para esos anuncios: texto del
> anuncio, lectura del modelo, lectura de las reglas (producto y precio
> detectados) y tres acciones (aceptar modelo, aceptar reglas, descartar).
> No toques el motor: el estado y el carril salen de las funciones
> existentes. Si algún cambio roza el motor de validación, pará y avisá.
> `docs/DESIGN.md` primero; mobile first.

### T11b — Tope de filas por anuncio, medido · M · opus

**Estado:** `lane1_max_rows=4` (`lane1.py:107`), 44 anuncios frenados solo por
esto. Pensar en el catálogo futuro es razonable, pero el tope es una decisión
de **riesgo por anuncio**, no de tamaño del catálogo.
**Alcance:** medir con la auditoría el error por tamaño de propuesta (1, 2, 3,
4, 5+ filas); proponer el tope con cota superior ≤ 10 %; registrar en
`decisions.md`. Este slice toca el motor de validación: flujo completo.

> **Prompt.** [prefijo] Slice `tope-filas-carril1`, flujo de especificación
> completo (toca el motor de validación). Con los eventos de auditoría del
> carril 1, medí el error por anuncio según el número de filas propuestas.
> Proponé un tope (o un tope que dependa del tamaño del catálogo) con la cota
> superior de error respetando la meta del 10 % (`lane1.py` `ERROR_TARGET`).
> No cambies el valor sin la aprobación del operador; registrá la decisión en
> `docs/decisions.md` reemplazando explícitamente la del 2026-10-13.

### T11c — Canal sucio no frena si modelo y reglas coinciden · S · opus

**Estado:** `engine.py:101` y `_NON_BLOCKING_REASONS` (`engine.py:120`): hoy el
canal sin historial no frena, el sucio sí (decisión G1b). Son 12 anuncios (8
frenados solo por esto).
**Alcance:** mover el motivo a la lista de no bloqueantes **solo** si el
modelo y las reglas coinciden y no hay otro motivo. Reemplaza una decisión
de G1b: va a `decisions.md`.

> **Prompt.** [prefijo] Slice `canal-sucio-no-frena`, flujo completo (toca el
> motor). Hoy un canal con historial sucio frena al anuncio (`engine.py:101`;
> decisión G1b, `docs/decisions.md` 2026-10-09). Propuesta del operador: si el
> único motivo es ese y modelo y reglas coinciden, el anuncio pasa. Escribí
> primero los casos (canal sucio + coinciden; canal sucio + otro motivo;
> canal sucio + no coinciden), implementalo y registrá la decisión que
> reemplaza a la de G1b. Medí con `python -m saimc.validation.lane1` cuántos
> anuncios cambian.

### T11d — Regla de «otra base» · L · opus

**Estado:** `rules.py:65`. Compara lecturas contra la mediana de una celda con
5 referencias, que es ruidosa; frena 22 (14 solo por esto).
**Alcance:** primero análisis con los datos curados: de los anuncios que la
regla marca, ¿qué fracción corrigió el operador a la otra base? Con eso
decidir entre subir el mínimo de referencias, exigir una distancia mínima, o
mostrar las dos lecturas al operador (T11a).

> **Prompt.** [prefijo] Slice `analizar-otra-base`, solo lectura primero.
> Para los anuncios marcados por `check_quantity_price` (`rules.py:65`)
> y ya curados por el operador, calculá en cuántos la base declarada por el
> modelo era la correcta y en cuántos la «otra». Estudiá cómo cambia el
> resultado con `min_references` mayor y con una distancia mínima entre
> lecturas (`cells.evaluate_basis`). Proponé la modificación con su error
> esperado; no la implementes sin aprobación. Si se implementa, flujo
> completo.

### T4 — Rediseño visual de `/control` · L · opus (prototipo) + sonnet (build)

**Estado:** `/control` está partido en 5 subpáginas (`control-nav`) con
mucho texto. **Pedido:** menos texto, más elementos visuales; un diagrama de
flujo estilo n8n con cada etapa (ingesta → pre-cálculo → carriles →
curaduría → publicación) y su estado.
**Dos pasos con puerta:**
1. **Prototipos** (HTML estático en `docs/prototipos/`, como los anteriores),
   con datos reales de ejemplo. **El operador aprueba antes de programar.**
2. Construcción con datos reales.
**Pregunta abierta para el prototipo:** qué se muestra por etapa (conteo,
cola, edad del más viejo, error auditado, último latido del timer).
Sin librerías de gráficos pesadas (decisión 1: render en servidor): SVG en
línea.

> **Prompt (paso 1).** [prefijo] Slice `control-visual-prototipo`. Leé
> `docs/DESIGN.md` y `docs/prototipos/`. No programes funcionalidad. Hacé 2 o
> 3 prototipos HTML estáticos del tablero `/control`: un diagrama de flujo
> tipo n8n con las etapas del sistema (ingesta WhatsApp/bandeja, pre-cálculo,
> carril 0, carril 1, cola de curaduría, tablero público, marketing) y, por
> etapa, un indicador visual (conteo, cola, edad del más viejo, error
> auditado, último disparo de timer). Menos texto, más gráficos (SVG en
> línea, sin librerías pesadas). Deja los prototipos en `docs/prototipos/` y
> parate: espero la aprobación del operador.

> **Prompt (paso 2, tras aprobación).** [prefijo] Slice `control-visual`.
> Implementá el prototipo aprobado en `docs/prototipos/<archivo>` sobre
> `/control`, con datos reales, SVG en línea y render en servidor. Verificá
> prototipo y página real renderizados lado a lado a 390 px y en escritorio.

### T10 — SEO puntual y analítica · M · sonnet

**Estado (reporte §9):** faltan `robots.txt`, `sitemap.xml`, canonical, og:image,
Twitter Cards, JSON-LD y favicon.
**Recomendación (con tradeoff):** **no construir un «módulo SEO»**. Con una
capa pública y un desarrollador, un parcial `_seo_head.html` + dos rutas +
un helper de JSON-LD cubren todo (1–2 jornadas). Un módulo solo aportaría
centralizar y testear, y el parcial ya lo da.
**Matomo:** no lo recomiendo en este VPS: PHP + MariaDB con 1,27 GB de RAM
libres y 85 % de disco, y su JS con cookies choca con `analytics.py`
(«sin JS de tracking, sin cookies») y con «sin datos personales». Mejor:
mantener `analytics.py` y agregar un script de resumen diario o GoAccess
sobre los logs; Umami (sin cookies, usa el Postgres existente) queda para
cuando se quiera un panel. *Estos requisitos y tamaños son de conocimiento
general, no se midieron en el VPS.*
**Antes de empezar, comprobar en producción:** el valor de
`SAIMC_SITE_GATE_ENABLED` (si está activo, no hay SEO posible) y qué está
delante de uvicorn (según Engram, Caddy con HTTPS; no consta en el repo).
**Verificación:** `/robots.txt` y `/sitemap.xml` responden; cada página tiene
canonical y og:image; el validador de datos estructurados no da errores;
Lighthouse SEO ≥ 90 en `/` y en un producto.

> **Prompt.** [prefijo] Slice `seo-basico`. Leé `docs/DESIGN.md`. Creá un
> parcial `_seo_head.html` (canonical sin parámetros de filtro, og:image con
> las tarjetas de marketing, Twitter Cards) usado por `_public_shell.html`;
> rutas `/robots.txt` y `/sitemap.xml` (generado desde `Product`); JSON-LD
> (`Organization`, `WebSite`, `Dataset` para `/datos.csv`, `BreadcrumbList`
> en producto); favicon y `apple-touch-icon`; `Cache-Control` para
> `/static`. No incluyas `/anuncio/<id>` en el sitemap (privacidad). No
> instales Matomo. Tests primero para cada ruta y cada etiqueta.

### T8a — Tarjetas diarias a disco, 6:00 hora de Cuba · M · sonnet

**Estado (reporte §9):** existen 9 tipos de tarjeta y render a PNG, pero el
PNG no se guarda y el contexto sale de un router.
**Alcance:** (1) extraer `_build_context` del router a `saimc.marketing`;
(2) un lote `saimc.marketing.daily` que elija producto/provincia del día y
tipo según el día de la semana, use los datos del **día anterior**, renderice
y guarde en `var/marketing/AAAA-MM-DD/`; (3) timer systemd. **Cuidado con la
zona horaria:** los timers existentes usan UTC; 06:00 en Cuba es 10:00 o 11:00
UTC según el horario de verano, así que no se puede fijar una hora UTC:
usar `America/Havana` explícito en `OnCalendar` (verificar que la versión de
systemd del VPS lo soporte). **Disco:** 85 % lleno; definir
retención (por ejemplo 30 días).
**Plantilla de identidad:** ya existe el estilo compartido
(`_marketing_card.html`); deuda: los colores de provincia están duplicados a
mano en `cards.py:20`.
**Fuera de alcance:** publicar (eso es T8b). Mientras tanto se sube a mano
desde Business Suite o con una herramienta de programación.
**Verificación:** `python -m saimc.marketing.daily --fecha 2026-10-15` deja N
PNG en disco con las cifras del 15; el timer figura en `systemctl list-timers`.

> **Prompt.** [prefijo] Slice `marketing-diario`. Leé `docs/DESIGN.md` y
> `docs/spec-slice-instagram.md`. (1) Sacá `_build_context` de
> `web/routers/marketing.py` a `saimc/marketing/`. (2) Creá
> `saimc.marketing.daily`: dado un día, elige producto y provincia, y qué
> tarjetas toca según el día de la semana (resumen diario, «cómo se movió»
> semanal, etc.), usa los datos del día anterior, renderiza con
> `marketing/render.py` y guarda los PNG en `var/marketing/AAAA-MM-DD/` con
> retención de 30 días. (3) Agregá `systemd/saimc-marketing.{service,timer}`
> a las 06:00 `America/Havana` (el patrón está en `tests/test_systemd_units.py`).
> No publiques nada. Tests primero con casos escritos a mano.

### T8b — Publicación automática en Instagram · L · opus

**Decisión del operador (2026-10-16):** publicar por API. El descarte del
2026-09-28 quedó revocado en `decisions.md`, donde también está lo que implica
para el sistema (permisos, URL pública de los medios, tabla de estado, kill
switch). Lo que sigue se mantiene como investigación previa: **nada se
construye hasta verificar contra la documentación vigente de Meta** qué
exige publicar con una cuenta propia. **Lo que se sabe**
(conocimiento general, vigencia **no verificada**): la API de Instagram con
Instagram Login permite publicar imagen, carrusel (hasta 10), Reels y
Stories con una cuenta profesional y sin página de Facebook; requiere el
permiso `instagram_business_content_publish` (**hay que regenerar el
token**); el medio se baja desde una **URL pública** y las imágenes deben
ser JPEG; hay un tope diario de publicaciones. Si Meta exige Advanced Access
o verificación de negocio para publicar, el riesgo es el mismo que ya
motivó el descarte. Facebook Pages exige página y revisión de app.
**Metricool (comparativa):** programa y publica en varias redes, con
analítica y calendario; evita tokens y revisión de Meta, pero suma un
tercero, una subida de archivos y un costo mensual (planes de pago, **precio
no verificado**). **Orden que propongo:** T8a primero (tarjetas a disco);
mientras se verifica Meta, se suben a mano desde Business Suite o con
Metricool; la API propia entra cuando la investigación confirme el acceso, y
las primeras semanas con **aprobación del operador antes de cada
publicación** (una publicación errónea es pública e irreversible en la
práctica).
**Falta construir:** cliente de publicación (`/media` y `/media_publish`),
tabla de estado (migración nueva), URL pública del medio, y regenerar el
token. Video: no hay generador diario (solo
`tools/producir_comercial.py`, manual).

> **Prompt (investigación).** [prefijo] Slice `investigar-publicacion-ig`,
> solo lectura y documentación. Con la documentación oficial vigente de Meta
> (verificala; no uses memoria), confirmá: permisos y acceso requeridos para
> publicar con Instagram Login, si hace falta App Review o verificación de
> negocio para una cuenta propia, formatos y límites, y qué exige Facebook
> Pages. Compará con Metricool (funciones, API, precios actuales). Entregá
> `docs/investigacion-publicacion.md` con recomendación y el texto de la
> entrada de `decisions.md` que reemplazaría a la del 2026-09-28. No
> implementes. (La revocación ya está registrada el 2026-10-16: si la
> verificación contradice sus supuestos, proponé una entrada que la corrija.)

### T16 — Tour de primera visita · M · sonnet

**Opciones:** tour propio de 60–120 líneas de JS y 2–3 KB de CSS con
`localStorage` (sin dependencia, encaja con conexión lenta), o Driver.js
vendorizado (~5 KB gzip, estimado). **Recomendación:** tour propio,
cargado solo en `public_home.html`. El riesgo mayor es anclar pasos a una
home de 170 KB con tres vistas: hacerlo después de T6 y T7. Actualizar la
política de privacidad si se guarda una marca en `localStorage`. Respetar
`prefers-reduced-motion` y modo oscuro.
**Verificación:** ventana privada → aparece el tour; terminar o saltar → no
reaparece al recargar; botón «Ver el tour otra vez»; 390 px sin scroll
horizontal.

> **Prompt.** [prefijo] Slice `tour-primera-visita`, después de
> `filtros-globales` y `rangos-3-7-90`. Leé `docs/DESIGN.md`. Tour propio en
> JS mínimo, solo en `public_home.html`, sin librerías externas ni CDN, con
> los tokens `--ph-*`. Pasos: qué es Guani, cambiar provincia y rango, leer
> una tarjeta de precio, entrar a un producto. Guarda `guani_tour` en
> `localStorage` (con try/catch: puede fallar), botón para repetirlo,
> `prefers-reduced-motion`, modo oscuro, 390 px. Actualizá la política de
> privacidad. Verificá en móvil y escritorio.

### T15 — Guía de crecimiento en redes · S · opus

**Alcance:** un documento en `docs/` que sirva de guía para quien no conoce
crecimiento de producto ni campañas: embudo, contenido por tipo de tarjeta,
cadencia, métricas que importan (las que el panel de Instagram ya mide),
comunidad, y un plan de 30 días. Con las restricciones del proyecto: sin datos
personales, conexión lenta del público cubano, fuera del MVP lo no validado.
Las cifras de plataformas deben citarse con fuente y fecha, o marcarse
como no verificadas.

> **Prompt.** [prefijo] Slice `guia-crecimiento`, solo documentación. Escribí
> `docs/guia-crecimiento.md` para un fundador sin experiencia en marketing:
> embudo (descubrir, visitar, volver), tipos de contenido para Instagram con
> los 9 formatos de tarjeta existentes, cadencia semanal, métricas que ya
> mide el panel de `/marketing`, cómo construir comunidad, y un plan de 30
> días. Usá `marketing:campaign-plan` como estructura. Citá fuentes y fechas;
> marcá «no verificado» lo que no puedas comprobar. Sin datos personales.

### T14 — Análisis de fase y transición · M · opus

**Estado:** reporte §1. Alcance cumplido, H0 medido pero no oficial,
criterio reemplazado. **Criterio del operador (2026-10-16):** la transición
se decide cuando exista la capacidad de validar una fila (T0) y, si las
condiciones son correctas, entonces se corre la H0 oficial. No antes. También
depende de T1: con tasas de relleno, las cifras de precio de H0 no son
confiables. **Entregable:** `docs/analisis-fase.md` con medición completa contra
`spec.md` §9 y §12.5 (métricas), la propuesta de criterio de transición, y la
entrada de `decisions.md` correspondiente.

> **Prompt.** [prefijo] Slice `analisis-fase`, solo lectura y documentación.
> Con `docs/spec-map.md`, `spec.md` §9 y §12.5 y los datos reales (SELECT),
> medí cada criterio de salida de Fase 0 y de entrada de Fase 1. Repasá
> `docs/reporte-estado-2026-10-16.md`. Proponé: definición de «validado»,
> si se hace la corrida oficial de H0, y el criterio de transición. No
> declares la transición: es del operador.

### T12 — ¿Vale la pena un paper? · S · opus

**Opinión inicial:** sí tiene valor, con alcance acotado. El problema (datos de
precios del mercado informal cubano, sin fuente oficial), la validación
híbrida (modelo + reglas + auditoría humana con cota de error medida) y la
medición honesta son un caso defendible como *informe técnico* o *working
paper*, útil para credibilidad con inversores, organismos y la comunidad. No
conviene hacerlo **antes** de tener cifras estables: la precisión completa es
0,388 en la última corrida. Mejor momento: después de T9 y T14.

> **Prompt.** [prefijo] Slice `esquema-paper`, solo documentación. Proponé
> el esquema de un informe técnico sobre Guani: problema, método de captura y
> validación, medición (H0 y auditoría) y limitaciones. Indicá qué cifras
> faltan, para qué revista, congreso o repositorio sirve y qué riesgos de
> privacidad hay. No escribas el paper; entregá el esquema.

## 5. Qué no entra y por qué

- C2, C4, C5 y A1c siguen fuera del MVP (`decisions` del 2026-10-10).
- Containerizar la app (decisión cerrada 4).
- Matomo, módulo SEO propio, y publicación automática en Facebook.

## 6. Riesgos del plan

- **T1 sobre producción:** el ensayo se hace sobre un volcado restaurado en una
  base temporal, con respaldo previo. Sin ensayo no se aplica.
- **Jev decisor (T3):** si decide sin haber medido el acuerdo de las señales
  vigentes, un error pasa a ser un descarte automático.
- **6 ramas sin push** (reporte §3): hacerlo antes de abrir más trabajo.
- **Disco al 85 %** (T8a): definir retención antes de generar PNG diarios.
- **T8b** puede quedar bloqueada por Meta; por eso es opcional.
