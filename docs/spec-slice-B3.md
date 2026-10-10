# Slice B3 — Conjunto de referencia congelado y métrica de la Fase 0 redefinida

Parte del plan `docs/plan-factor-humano.md` (B3) y tarea T6 de
`docs/plan-mvp-inversores.md`. Toca el esquema de datos: esta especificación se
aprueba antes de programar.

**Estado: PROPUESTA, sin aprobar.** Nada de lo que sigue está decidido. Las
preguntas del final las contesta el operador; cada una trae una recomendación.
No se programó ni se migró nada, y los apartamientos de la sección
«Apartamientos propuestos» no están escritos en `docs/spec-map.md` ni en
`docs/decisions.md`.

## La idea que lo ordena

Hoy un solo número, H0, carga con dos preguntas distintas:

1. **¿Qué tan bien lee el extractor?** Es una pregunta sobre código. Se contesta
   contra etiquetas que no cambian, para que el número se mueva solo cuando se
   mueve el extractor.
2. **¿Cuánto del dato se produce sin una persona, y con qué error?** Es una
   pregunta sobre el sistema andando. Se contesta contra lo que entró y lo que
   se auditó.

B3 las separa. La primera queda en H0, medida contra un conjunto congelado. La
segunda pasa a ser la métrica de la Fase 0: la tasa de automatización con su
error auditado al lado.

## Problema

Lo que hay hoy, leído del código:

- **La marca no existe.** `spec.md` §6.1 pide que la cola permita «marcarlo como
  parte del conjunto de referencia». No hay columna, tabla ni archivo que diga
  qué anuncios son la referencia.
- **El oro se recalcula en cada corrida.** `extraction/measure.py` toma todo
  anuncio `curated` con filas en `prices_history` que no pasó por el asistente
  (`assisted_ingestion_ids`) y compara contra las filas **tal como están hoy**.
- **El oro se puede mover sin que nadie lo decida.** Lo cambian:
  `POST /curation/{id}/edit` (actualiza, borra y agrega filas),
  `POST /curation/{id}/delete` (borra las filas y descarta el anuncio) y
  cualquier script suelto. Ya pasó: el 2026-10-05 se insertaron 7 filas en
  cuatro anuncios del oro, propuestas por el modelo (`docs/decisions.md`, «Siete
  filas de pollo y picadillo…»). Esos cuatro anuncios siguen contando como
  etiquetados a mano.
- **El oro ya no crece.** Desde que el pre-cálculo propone para toda la cola, lo
  que el operador cura lo cura con la propuesta a la vista: nace asistido y
  queda fuera (`docs/plan-asistente-llm-cola-completa.md`, «Pendiente — qué
  significa H0 ahora»). El backtest v7 del 2026-10-05 contó **220 anuncios** de
  oro: 147 textos y 73 imágenes. `spec.md` §3 pide 300.
- **H0 mide una pieza, no el sistema.** `measure.py` re-corre `propose_all`, el
  extractor por reglas. En producción la propuesta la hace el modelo, el carril
  0 descarta solo y el carril 1 publica solo. Ninguna de esas tres cosas mueve
  H0.
- **«Tasa de automatización» ya existe y cuenta otra cosa.**
  `progress.WeekAutomation.automation_rate` (A2d) es «resueltos que no pasaron
  por el formulario individual» sobre «resueltos en la semana». Cuenta como
  automatizados los lotes que el operador dispara a mano
  (`batch_out_of_focus`, `batch_model`), y su denominador ignora lo pendiente:
  si el operador no cura nada en una semana, da 100 %.

**Lo que no se pudo verificar.** No se consultó la base de producción en esta
sesión (el acceso a la URL de la base fue denegado por el clasificador de
permisos). Las cifras de arriba salen de `docs/h0-runs.jsonl`, de
`docs/decisions.md` y del archivo de resultados del backtest v7. Cuántos
anuncios entran de verdad al conjunto lo dice el comando de congelado en seco
(sección 1), antes de escribir nada.

## Qué se construye

### 1. Cómo se congela: tabla de solo altas, con los valores y su hash

**Decisión propuesta: se guardan los valores, no solo el hash.**

El plan decía «una lista de ids con el hash de sus valores». Con solo el hash, el
día que `/edit` toca un congelado se sabe que cambió, pero la etiqueta original
ya no está: quedan dos salidas, sacar el anuncio (el conjunto se achica) o
aceptar el valor nuevo (el oro cambió). Guardando los valores hay una tercera,
que es la que se quiere: seguir midiendo contra la etiqueta congelada y decidir
aparte. El hash se conserva, para detectar la deriva con una comparación barata.

Tabla nueva `reference_set_members`, migración `0031`:

| Columna | Tipo | Qué guarda |
| --- | --- | --- |
| `id` | UUID, PK | |
| `set_version` | SMALLINT, NOT NULL | Versión del conjunto. La primera es 1. |
| `ingestion_id` | UUID, NOT NULL, FK a `raw_ingestions` con RESTRICT | El anuncio. |
| `rows` | JSON, NOT NULL | Las filas confirmadas al congelar, una por fila de `prices_history`. |
| `values_hash` | VARCHAR(64), NOT NULL | SHA-256 de `rows` en forma canónica. |
| `frozen_at` | TIMESTAMPTZ, NOT NULL | Cuándo se congeló. |

Con `UNIQUE (set_version, ingestion_id)`.

- **Qué lleva cada fila de `rows`:** `price_history_id` (como texto, sin clave
  foránea) y los seis campos contra los que compara `row_correctness`:
  `product_id`, `province_id`, `price`, `currency`, `quantity`, `unit`. Nada
  más. Como en `curation_events.detail`: nunca texto del anuncio, canal ni
  emisor (`spec.md` §10.6).
- **Forma canónica:** las filas ordenadas por `price_history_id`, las claves
  ordenadas, los decimales sin ceros de más (`340.00` y `340` dan el mismo
  hash), la unidad pasada por `canonical_unit`, nulo como `null`. El hash no
  puede depender del motor de base: los tests corren sobre SQLite y producción
  es PostgreSQL.
- **Hash del conjunto:** SHA-256 sobre las líneas `ingestion_id:values_hash`
  ordenadas. Es la huella que se anota en cada corrida y en `docs/decisions.md`
  al congelar.
- **Solo altas.** El código no tiene camino de escritura sobre una fila
  existente ni de borrado, igual que `curation_events`. Una versión nueva vuelve
  a insertar todos sus miembros con el `set_version` siguiente; son unas 220
  filas chicas por versión.

**Por qué una tabla y no otra cosa** (descartado):

- *Columna en `raw_ingestions`:* la tabla es inmutable salvo sus metadatos de
  procesamiento, y una columna booleana no guarda valores ni versión.
- *Columna en `prices_history`:* `/edit` borra filas, y la marca se iría con
  ellas. Es exactamente el caso que hay que resistir.
- *Archivo en git, como `docs/h0-runs.jsonl`:* el conjunto es estado de los
  datos, no del código. Un archivo se separa de la base en un backup o en un
  restore, y dejaría ids de producción en el repositorio que se sirve en `/docs`.
- *Dos tablas (conjunto y miembros):* los metadatos de la versión son la fecha,
  el criterio y los excluidos. La fecha sale de `frozen_at`; el criterio y los
  excluidos van en la entrada de `docs/decisions.md` que acompaña cada
  congelado. Una tabla más no agrega nada.

**Quién entra a la versión 1.** Es una regla, no una elección anuncio por
anuncio:

1. `processing_status = curated`;
2. tiene al menos una fila en `prices_history`;
3. no pasó por el asistente: no tiene fila en `llm_reviews` ni está en el
   registro del operador (el mismo `measure.assisted_ingestion_ids` de hoy). Eso
   deja afuera también todo lo que aceptó el carril 1, que escribe su revisión;
4. no está en la lista de excluidos que se le pasa al comando.

**Excluidos propuestos para la versión 1:** los cuatro anuncios con las 7 filas
insertadas el 2026-10-05 (`ab9bb650`, `0944e4dd`, `8442a0dd`, `e08f6cc2`). Su
etiqueta ya no es independiente del modelo y la entrada de ese día manda
decirlo o dejarlos afuera.

**Comando nuevo** `python -m saimc.extraction.reference_set`:

- Sin argumentos corre **en seco**: dice cuántos anuncios y filas entrarían, por
  tipo, y cuántos quedan afuera por cada motivo (asistidos, sin filas,
  excluidos a mano). No escribe.
- `--execute` escribe la versión siguiente e imprime el hash del conjunto.
- `--exclude <id>` (repetible) deja afuera un anuncio. Acepta el prefijo de 8
  caracteres si es único.
- `status` imprime la versión vigente, su n, su hash y los miembros con deriva.

Va a `docs/comandos.md` en el mismo commit.

**Qué no se congela, y se dice en cada corrida.** El conjunto fija las
etiquetas. No fija lo que rodea al extractor: el catálogo del foco
(`pilot_products`), la provincia fija de cada canal y el motor de OCR. Un
cambio ahí mueve H0 sin que cambie una regla. No es nuevo, hoy pasa igual; la
corrida ya anota el commit y la versión de Tesseract.

### 2. Qué pasa cuando `/edit` toca un curado congelado

**Decisión propuesta: la edición se permite y no cambia la referencia.**

- `/edit` y `/delete` siguen funcionando igual sobre un anuncio congelado. Lo
  publicado tiene que poder corregirse: un precio mal cargado en el tablero no
  puede quedar ahí para proteger una medición.
- La copia congelada no se toca. H0 sigue midiendo contra ella.
- **Deriva:** un miembro deriva cuando el hash de sus filas vivas ya no coincide
  con su `values_hash` (incluye el anuncio borrado o descartado después). La
  medición cuenta los miembros con deriva y los lista con `status`. No los
  saca del conjunto ni corta la corrida.
- **La referencia solo cambia con una versión nueva.** Si la edición corrigió un
  error de etiquetado, el arreglo entra al oro cuando el operador congela la
  versión siguiente, que toma los valores vivos de ese día. Es un acto
  explícito, con su entrada en `docs/decisions.md` (qué anuncios cambiaron y por
  qué). Dos corridas con versiones distintas no se comparan sin decirlo.
- **Aviso en pantalla:** la edición de un anuncio congelado muestra una línea,
  «Este anuncio está en el conjunto de referencia (versión N): la corrección
  cambia lo publicado, no la referencia». No bloquea ni pide confirmación.

Descartado:

- *Bloquear la edición:* deja publicado un error conocido.
- *Que la edición actualice la referencia:* es lo de hoy. El oro se mueve sin
  que nadie lo decida, y un número de H0 deja de ser comparable con el anterior.
- *Sacar del conjunto al miembro editado:* el conjunto se achica con cada
  corrección, y los que se corrigen no son una muestra al azar.

El costo, dicho de frente: si una etiqueta congelada estaba mal, H0 la sigue
contando como verdad hasta la versión siguiente. Congelar fija el conjunto, no
lo mejora. Ya hay una sospecha de oro incompleto (21 anuncios de «solo filas
borradas», `docs/decisions.md`, 2026-10-05, «Oro v7»).

### 3. H0: el extractor contra el conjunto congelado

`measure.py` deja de armar el oro en cada corrida:

- Los anuncios evaluados son los miembros de la versión vigente (la mayor).
- Las filas confirmadas salen de `rows` de cada miembro, no de `prices_history`.
  `row_correctness` y el emparejado no cambian: reciben un objeto chico con los
  mismos seis campos.
- **Sin conjunto congelado, el comando se niega a correr** y dice cuál es el
  comando de congelado. No hay camino de vuelta al oro vivo.
- La tasa de descarte y los conteos de composición (`total_curated`,
  `total_discarded`, `discarded_off_focus`, `discard_rate`) siguen saliendo de
  la base viva: describen el corpus, no la referencia.
- `assisted_ads_count` y `assisted_rows_count` dejan de tener sentido como
  «excluidos por asistencia»: la pertenencia ya está fijada. Se siguen
  escribiendo, en 0, para no romper a quien lee el archivo.
- `is_official` no cambia de definición: verdadero con 300 anuncios evaluados o
  más (`spec.md` §3). Con unos 216 va a dar falso en todas las corridas, y eso
  es una pregunta para el operador (pregunta 4), no algo que este slice resuelva
  bajando el número.

**Registro de la corrida.** `docs/h0-runs.jsonl` sigue siendo de solo anexado y
**las diez líneas viejas no se tocan**. Las líneas nuevas suman claves:

| Clave | Qué es |
| --- | --- |
| `reference_set_version` | Versión del conjunto contra el que se midió. |
| `reference_set_hash` | Huella del conjunto. |
| `reference_set_drifted` | Miembros con deriva el día de la corrida. |
| las de la sección 5 | La foto de la métrica de la Fase 0. |

Una línea sin `reference_set_version` es una corrida contra el oro vivo de su
día. `read_h0_runs` ya tolera claves ausentes.

**El salto en la serie.** La última corrida vieja midió 194 anuncios vivos
(2026-09-09); la primera nueva va a medir unos 216 congelados. La diferencia
entre esas dos no es atribuible al extractor. `complete_delta` pasa a ser nulo
cuando la corrida y su anterior no comparten conjunto (una línea vieja no
comparte conjunto con ninguna nueva), y `/control` muestra la versión del
conjunto en cada fila del historial.

### 4. La métrica de la Fase 0: una partición, dos números

Todo anuncio que entró cae en **exactamente un casillero**, por su estado de
hoy y por el modo del evento que lo resolvió:

| Casillero | Definición |
| --- | --- |
| **A** — publicado solo | `curated`, con un evento `curated` de modo `auto` (lo aceptó el carril 1) |
| **H** — publicado por el operador | `curated`, sin ese evento |
| **D0** — descartado solo | `discarded`, y su último evento de descarte es de modo `auto` (carril 0) |
| **Dh** — descartado por el operador | `discarded`, cualquier otro caso (individual, por lote, o borrado después de curar) |
| **P** — pendiente | `pending` |

A + H + D0 + Dh + P = todo lo que entró. Los casos de borde salen solos de mirar
el estado de hoy:

- Un descarte del carril 0 que la auditoría rescató vuelve a `pending`: cae en
  P, y después donde lo deje el operador.
- Un aceptado solo que la auditoría marcó «no era válido» queda `discarded` por
  una decisión individual: cae en Dh.
- Un aceptado solo que la auditoría corrigió sigue `curated`: cae en A. Se
  publicó solo y estaba mal; eso lo dice el error auditado, no la partición.
- Un lote que dispara el operador (`batch_out_of_focus`, `batch_model`) cae en
  Dh. Es trabajo del operador, más barato, pero no es «sin el operador». Acá
  esta cuenta se separa de `WeekAutomation.automation_rate`, que no se toca.
- Lo anterior a la bitácora (2026-10-02) no tiene eventos: cae en H o en Dh,
  que es lo que fue.

**Unidad:** el anuncio (`raw_ingestions`), no la fila. Las filas por procedencia
ya se publican aparte (`lane1.provenance_counts`).

**«Válido»** quiere decir: terminó `curated`, o sea que produjo al menos una
fila de precio. Un anuncio con dato económico pero fuera de foco no es válido
para esta cuenta.

De la partición salen los dos números que hoy se mezclan:

**N1 — Resuelto sin el operador.** De todo lo que entra, cuánto se resuelve
solo.

```
N1 = (A + D0) / (A + H + D0 + Dh + P)
```

Cuenta los descartes del carril 0, y lo pendiente va en el denominador. Es alto
porque la mayor parte de lo que llega no es un anuncio de un producto piloto.

**N2 — Publicado sin el operador.** De los anuncios válidos, cuántos se publican
solos. No cuenta los descartes del carril 0.

El denominador exacto no se conoce mientras haya pendientes: no se sabe cuántos
de P son válidos. En vez de estimarlo con el modelo, se publica lo que sí se
sabe:

```
N2 es al menos  A / (A + H + P)     (si todos los pendientes fueran válidos)
N2 es a lo sumo A / (A + H)         (si ninguno lo fuera)
```

Cuando la cola de una cohorte se vacía, P es 0 y los dos extremos coinciden: el
número queda exacto.

Descartado para el denominador de N2:

- *Solo lo ya resuelto, A / (A + H), como número único:* el carril 1 resuelve en
  horas y el operador en días, así que con cola larga el número sale inflado.
- *Válidos estimados por el modelo* (pendientes con filas propuestas, la cuenta
  de «73 de 497» del plan del MVP): es una estimación, y a un tercero se le dice
  lo medido, nunca lo estimado (plan de factor humano, «Adelantar G1b»). Puede
  mostrarse en `/control` como referencia interna, rotulada como estimación.

**Ventana: cohorte por semana de ingreso.** Cada anuncio pertenece a la semana
(lunes a domingo, la misma del panel de A2d) de su `raw_ingestions.created_at`.
La partición se calcula por cohorte, con el estado de hoy. Se muestran las
últimas 8 semanas cerradas, la semana en curso marcada como parcial, y un total
desde la semana del primer evento de modo `auto`.

Descartado: *agrupar por semana de decisión.* Es lo que hace A2d. No ve la cola:
una semana sin curaduría manual da 100 %.

**Por tipo.** La partición se abre en texto e imagen. Hoy ninguna imagen entra
al carril 1, y un N2 único esconde eso.

### 5. Cómo se publica junto al error auditado

Regla: **N1 y N2 nunca se muestran sin el error auditado de lo que cuentan, con
su n, en el mismo bloque.** Un número de automatización solo es una promesa.

| Lo que se cuenta | Su error auditado | De dónde sale |
| --- | --- | --- |
| D0, descartado solo | Falsos descartes: rescatados sobre auditados, por tipo, con cota superior al 95 % | `lane0.audit_bounds` (existe) |
| A, publicado solo | Error por anuncio: corregidos o inválidos sobre auditados, con cota superior al 95 % | `lane1.audit_state` (existe) |

Las metas que se muestran al lado son las que ya decidió el operador: 10 % y
20 % de falsos descartes para texto e imagen (G1a) y 10 % de error por anuncio
(G1b). **Este slice no agrega ninguna meta ni ningún umbral.**

Si de algo no hay nada auditado, el bloque dice «sin auditar todavía» y el
número sale rotulado como provisional.

No se publica un número compuesto (del tipo «publicado solo y correcto»).
Multiplicar una tasa por una cota da una cifra con apariencia de medición que
nadie midió.

Una sola función arma todo (`automation_summary`, código puro sobre una
consulta) y la leen tres lugares:

- **`measure.py`**: imprime primero «Métrica de la Fase 0» (la partición total,
  N1, los dos extremos de N2 y los dos errores auditados) y después «H0 —
  extractor sobre el conjunto congelado, versión N». La foto va a la línea de
  `docs/h0-runs.jsonl`: los cinco conteos, N1, los dos extremos de N2, y por
  cada carril los auditados, los errores y la cota.
- **`/control`** (operador): el bloque con la serie por cohorte, por tipo.
- **`/metodologia`** (pública): el total, con los dos números rotulados de modo
  que no se confundan, y debajo de cada uno su error auditado. Suma lo que hoy
  no es público: los falsos descartes del carril 0. Solo conteos; ningún canal
  ni anuncio. Es interfaz: se decide a 390 px primero y el operador la compara
  contra un prototipo aprobado.

### 6. Qué cambia en el runbook de H0

`docs/runbook-medir-h0.md` se reescribe en el slice que cambia `measure.py`:

- **Sección nueva, «Congelar el conjunto»:** se hace una vez. En seco, leer los
  conteos, `--execute`, anotar el hash en `docs/decisions.md`.
- **«Antes de correr»** suma: tiene que existir un conjunto congelado.
- **«Mirá en este orden»** cambia el orden: primero la métrica de la Fase 0 (los
  dos números con su error), después H0. Se va «Excluidos por asistencia» y
  entra «Miembros con deriva»: qué quiere decir y que no se arregla congelando
  de nuevo.
- **Sección nueva, «Versión nueva del conjunto»:** cuándo corresponde (un error
  de etiquetado confirmado, o anuncios nuevos etiquetados sin asistencia) y que
  rompe la comparación con las corridas anteriores.
- **«Qué NO hacer»** suma: no congelar una versión nueva para mover un número.
- La tabla de campos del registro suma las claves nuevas y aclara cómo se lee
  una línea vieja.

## Casos esperados (se escriben antes que el código)

Sintéticos, con la respuesta definida acá y no derivada del código.

**Congelar**

1. Un curado a mano con 2 filas y sin revisión del modelo entra: un miembro, con
   2 filas en `rows`.
2. Un curado con una fila en `llm_reviews` no entra, y el modo en seco lo cuenta
   como «asistido».
3. Un anuncio aceptado por el carril 1 y después auditado «está bien» no entra.
4. Un `curated` sin filas en `prices_history` no entra.
5. Un id pasado con `--exclude` no entra, y el modo en seco lo cuenta aparte.
6. El modo en seco no escribe ninguna fila.
7. Dos filas con `price` 340.00 y 340, y lo demás igual, dan el mismo hash.
8. El mismo anuncio con sus filas leídas en otro orden da el mismo hash.
9. Congelar dos veces sin cambios escribe las versiones 1 y 2 con el mismo hash
   de conjunto; la versión 1 queda intacta.

**Editar un congelado**

10. Se edita el precio de una fila de un miembro: `prices_history` cambia, `rows`
    del miembro no, y `status` lo lista con deriva.
11. Se borra una fila de un miembro desde `/edit`: deriva; `rows` conserva la
    fila.
12. Se elimina el anuncio con `/delete`: el miembro sigue en el conjunto, con
    deriva.
13. Una edición sin cambios no produce deriva.
14. Después de 10, una versión nueva toma el precio corregido; la versión 1
    sigue con el viejo.

**Medir H0**

15. Sin ningún conjunto congelado, `measure` termina con error y nombra el
    comando de congelado. No escribe en `docs/h0-runs.jsonl`.
16. Con un miembro cuyo precio congelado es 100 y cuyo precio vivo se editó a
    120, un extractor que propone 100 acierta el precio.
17. Un curado a mano posterior al congelado no se evalúa.
18. La línea registrada trae `reference_set_version`, `reference_set_hash` y
    `reference_set_drifted`.
19. Leyendo un archivo con una línea vieja (sin versión) seguida de una nueva,
    `complete_delta` de la nueva es nulo.
20. Las líneas ya escritas del archivo quedan byte por byte iguales después de
    una corrida.

**Partición**

21. Cohorte con 10 anuncios: 5 descartados por el carril 0, 1 aceptado por el
    carril 1, 1 curado a mano, 1 descartado a mano y 2 pendientes. N1 = 6/10.
    N2 es al menos 1/4 y a lo sumo 1/2.
22. Un descarte del carril 0 rescatado y todavía pendiente cuenta en P, no en
    D0.
23. Un aceptado solo que la auditoría marcó inválido cuenta en Dh; sale del
    numerador de N1 y de N2.
24. Un aceptado solo corregido en la auditoría cuenta en A.
25. Un descarte por lote sin foco cuenta en Dh, no en D0.
26. Una cohorte sin pendientes da los dos extremos de N2 iguales.
27. Una cohorte sin ningún válido ni pendiente (A + H + P = 0) da N2 «sin
    datos», no 0 %.
28. Los cinco casilleros de una cohorte suman siempre su total.

## Apartamientos propuestos — sin escribir

Se escriben al implementar, en el commit del slice que los vuelve ciertos, y
solo si el operador aprueba. Texto propuesto:

**Filas para la tabla de `docs/spec-map.md`:**

| Tema | Especificación | Implementado |
| --- | --- | --- |
| Métrica de la Fase 0 | §12.5: las métricas de extracción son «las únicas relevantes durante la Fase 0» | La métrica de la Fase 0 es la tasa de automatización (resuelto sin el operador y publicado sin el operador) con su error auditado. H0 queda como medición del extractor por reglas sobre el conjunto congelado |
| Marca de conjunto de referencia | §6.1: la cola «permite marcarlo como parte del conjunto de referencia» | El conjunto se congela por regla y por lote, con un comando, y se versiona. No hay marca por anuncio: elegir a mano qué entra sesga la referencia |
| Tamaño del conjunto de referencia | §3: al menos 300 anuncios | *Solo si la pregunta 4 se contesta como se recomienda:* el conjunto se congela con los anuncios etiquetados sin asistencia que existen (unos 216), y el número se informa con su n. `is_official` sigue en falso |

**Fila que se corrige** (hoy dice que queda fuera «como todo lo asistido»):
«Anuncios aceptados solos y H0» pasa a decir que quedan fuera del conjunto de
referencia también después de auditados, porque el conjunto es cerrado y la
auditoría confirma una lectura del modelo.

**La fila de «Antes de proponer»** de §12.5 pasa a decir «Apartado 2026-08-12 y
B3». La de §3 suma B3 si la pregunta 4 se aprueba.

**Entrada para `docs/decisions.md`** (borrador; los números los pone la corrida
real):

```
## AAAA-MM-DD — B3: el conjunto de referencia se congela y la métrica de la Fase 0 pasa a ser la automatización con su error auditado
Contexto: el oro de H0 se rearmaba en cada corrida desde las filas vivas, así que `/edit` y cualquier script lo movían; ya no crecía porque todo lo que se cura nace asistido; y H0 mide el extractor por reglas, que dejó de describir un sistema donde el carril 0 descarta solo y el carril 1 publica solo.
Decisión: tabla `reference_set_members` de solo altas, con los valores y su hash por anuncio; versión 1 con N anuncios (hash …), sin los cuatro del 2026-10-05. `measure.py` mide H0 contra esa copia. Editar un congelado cambia lo publicado y no la referencia. La métrica de la Fase 0 son dos números por cohorte de ingreso, cada uno con su error auditado: resuelto sin el operador y publicado sin el operador. Amplía la entrada del 2026-08-12, no la reemplaza.
Descartado: guardar solo el hash (una edición deja sin etiqueta original); bloquear la edición (deja publicado un error conocido); un único número de automatización (mezcla el filtro de ruido con la producción de dato); estimar los válidos pendientes con el modelo (a un tercero se le dice lo medido).
Consecuencia: las corridas de `docs/h0-runs.jsonl` anteriores a esta no son comparables con las nuevas y no se reescriben. Un error de etiquetado congelado sigue contando hasta la versión siguiente. El conjunto no llega a los 300 de §3.
```

**`CLAUDE.md`** lo edita o aprueba el operador. Dos pasajes quedan desfasados:
la sección «H0 — métrica de eficiencia» (dice que todo el trabajo de la Fase 0
existe para producir el número de H0) y la regla de «Código» que manda medir
H0 «contra el conjunto de 300+ anuncios reales etiquetados a mano, vía la cola
de curaduría». La redacción se propone al cerrar el slice que cambia
`measure.py`.

## Qué no entra

- **Etiquetar a ciegas** (curar sin la propuesta a la vista) para llegar a 300.
  No existe ese modo en la cola. Depende de la pregunta 4.
- **El backtest del modelo** (`llm_backtest.select_gold`) sigue armando su oro
  como hoy. Cada archivo de resultados ya lleva su propia copia de las filas
  esperadas. Unificarlo con el conjunto congelado es un pendiente aparte: toca
  la comparabilidad del informe de sombra y una corrida gasta API.
- **`WeekAutomation.automation_rate`** (A2d) no se toca ni se renombra en este
  slice. Queda como «resuelto sin formulario individual», que es otra cuenta.
- La capa pública por celda (B4).
- Ninguna meta nueva. La del nivel 3 de `docs/plan-mvp-inversores.md` sigue en
  blanco.
- Reescribir `docs/h0-runs.jsonl`.

## Costo

Ninguno de API. `measure.py` sigue re-corriendo el OCR local sobre las imágenes
del conjunto.

## Orden de entrega

Cuatro slices, cada uno en su worktree.

| Slice | Qué | Casos | Modelo sugerido |
| --- | --- | --- | --- |
| B3-1 | Migración `0031`, modelo, forma canónica y hash, comando de congelado (en seco, `--execute`, `--exclude`, `status`), `docs/comandos.md` | 1–14 | Opus: es el esquema |
| B3-2 | `measure.py` contra el conjunto, claves nuevas del registro, `complete_delta`, versión en el historial de `/control`, runbook reescrito, apartamientos de H0 | 15–20 | Opus: es el instrumento de medición |
| B3-3 | La partición (`automation_summary`), su sección en `measure.py` y el bloque de `/control` | 21–28 | Sonnet: la definición ya está cerrada acá |
| B3-4 | `/metodologia` con los dos números y su error auditado, el aviso en la edición de un congelado, apartamiento de §12.5 y texto propuesto para `CLAUDE.md` | — | Sonnet |

B3-3 no depende de B3-1 ni de B3-2. Si hablar con inversores está cerca, puede ir
primero: es el número que se muestra. Congelar es lo que evita que el oro se
siga moviendo mientras tanto, y cada día sin congelar es un día más de ediciones
posibles sobre él.

Después de B3-1 hay un paso que es del operador y no es desarrollo: correr el
congelado en seco en el servidor, mirar los conteos y ejecutarlo.

## Preguntas al operador

1. **¿Se guardan los valores congelados, además del hash?**
   Recomendación: sí. Sin los valores, una edición deja el conjunto sin su
   etiqueta original.

2. **¿Qué entra a la versión 1?**
   Recomendación: todo curado sin asistencia y con filas, al día en que se corra
   el comando, **menos** los cuatro anuncios con las 7 filas del 2026-10-05.

3. **¿Qué pasa cuando `/edit` toca un congelado?**
   Recomendación: se permite, cambia lo publicado y no la referencia, y la
   deriva queda a la vista. La referencia solo cambia con una versión nueva.

4. **El conjunto no llega a 300 (hay unos 216) y ya no crece. ¿Qué se hace con
   los 300 de `spec.md` §3?**
   Recomendación: congelar lo que hay, informar H0 con su n, dejar `is_official`
   en falso y no construir el etiquetado a ciegas ahora. Es un apartamiento de
   §3, y cambia cómo se lee el criterio de salida de §9: «H0 respondida con un
   número» pasa a cumplirse con el número sobre el conjunto congelado, diciendo
   que no llega a 300. La alternativa es un modo de la cola que cure sin mostrar
   la propuesta, unos 85 anuncios más de trabajo manual sin asistencia.

5. **¿Un anuncio aceptado solo entra a H0 una vez auditado?**
   El plan y la especificación de B2b dicen «queda fuera hasta que se audite».
   El código hace otra cosa: la auditoría escribe una revisión, así que queda
   fuera siempre. Recomendación: que quede fuera siempre, y corregir el texto.
   La auditoría confirma una lectura del modelo; no es una etiqueta
   independiente. Lo que la auditoría alimenta es el error auditado.

6. **¿A cuál de los dos números se le dice «tasa de automatización»?**
   Recomendación: a ninguno suelto. Se publican los dos con su nombre,
   «Resuelto sin el operador» (N1) y «Publicado sin el operador» (N2), y cuando
   haya que elegir un titular es N2: N1 lo empuja el descarte de ruido.

7. **¿Cómo se muestra N2 mientras hay pendientes?**
   Recomendación: en público, el piso («al menos X %», contando todos los
   pendientes como válidos) con los conteos al lado. El techo, solo en
   `/control`.

8. **¿La ventana es por semana de ingreso?**
   Recomendación: sí. Por semana de decisión el número ignora la cola.

9. **¿Se publican en `/metodologia` los falsos descartes del carril 0?**
   Hoy solo los ve el operador. Recomendación: sí. N1 cuenta esos descartes, y
   no puede salir sin su error al lado.

10. **¿Hay una meta para N2?**
    Sin recomendación de valor: `spec.md` §12.6 prohíbe inventarla. Es la meta
    del nivel 3 de `docs/plan-mvp-inversores.md`, que sigue en blanco. Este
    slice funciona sin ella.

11. **¿Cuatro slices en este orden, o B3-3 primero?**
    Recomendación: el orden de la tabla, salvo que la conversación con
    inversores esté cerca.
