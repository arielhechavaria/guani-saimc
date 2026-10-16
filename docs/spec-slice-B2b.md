# Slice B2b + B2c — Carril 1 encendido: aceptación automática, auditoría y freno

Parte del plan `docs/plan-factor-humano.md` (B2b y B2c se entregan juntas). Toca el
motor de validación: esta especificación se aprueba antes de programar.

**Estado: APROBADA por el operador el 2026-10-09, con las ocho respuestas por
defecto, e IMPLEMENTADA** en cuatro slices: B2b-1 (motor), B2b-2 (aplicar),
B2b-3a (auditoría y freno) y B2b-3b (lo público y la muestra semanal). Lo que
se apartó de este texto al implementar está en `docs/decisions.md`, en las
entradas del 2026-10-09 de cada slice: el guardado compartido es de tres piezas
y no del endpoint entero; el tope de 30 sin auditar es un máximo; el freno por
celda divergente mira producto y provincia; y `/control` pide operador, así que
lo público va en `/metodologia`.

Sale de G1b, que el operador decidió el 2026-10-09 por el camino «Adelantar G1b»
del plan: el carril 1 se enciende antes de llegar al n de la especificación, y el
error se mide andando, con auditoría y freno (`docs/decisions.md`, 2026-10-09,
«G1b se abre adelantada»).

## La idea que lo ordena

Lo principal es llevar de WhatsApp al sistema todo anuncio válido: el que trae
producto piloto y precio. Un anuncio válido no se frena por algo que el sistema
todavía no sabe (un canal que nadie curó, una celda con pocos precios). Sí se
frena cuando hay una señal de que **la lectura del modelo** puede estar mal,
porque lo que entra al sistema no es el anuncio sino esa lectura.

Lo que entra por el carril 1 **sale en el tablero público de inmediato**, sin
esperar auditoría (decisión del operador, 2026-10-09).

## Problema

1. El carril 1 existe en el motor pero nadie lo aplica, y tal como está no
   aceptaría casi nada: sobre los 497 textos pendientes con filas propuestas deja
   pasar 6.
2. Dos de sus frenos no hablan del anuncio. Sobre el oro v7, a los anuncios que
   **solo** frenaba el canal sin historial el operador no les tocó nada (0
   errores en 17), y los 14 errores del carril 1 son de un solo canal, que sí
   tiene historial y está sucio (Groupej Morales Surl: 19 de 27 filas tocadas).
3. No hay forma de guardar filas sin el operador, ni de auditarlas, ni de frenar
   el carril si se equivoca.

## Qué se construye

### 1. El motor: qué frena y qué no

Cambia **solo la decisión del carril**. Las seis reglas, el estado de cada fila y
`validate_stored_row` no cambian, y `ENGINE_VERSION` no se sube.

Dos resultados `unknown` dejan de sacar al anuncio del carril 1, igual que ya
pasa con el posible duplicado (`_DUPLICATE_REASON`):

| Regla | Resultado | Antes | Ahora |
| --- | --- | --- | --- |
| 4 (canal) | `unknown`: sin historial suficiente | frena | no frena |
| 2 y 6 (celda) | `unknown`: sin referencias con que comparar | frena | no frena |

La fila conserva su motivo y sale `unverified`, no `approved`.

Siguen frenando, sin cambios:

- el anuncio es una imagen;
- la propuesta está degradada, trae filas caídas o no trae ninguna fila;
- la cita del modelo no está verificada en el texto;
- reglas y modelo no coinciden en producto, precio, moneda o provincia;
- el modelo se abstuvo en cantidad, unidad o base del precio;
- regla 1 fallada (falta producto piloto, precio, moneda o provincia);
- regla 2 o 6 **fallada**: el precio cae fuera de la banda de su celda, o leído
  con la otra base queda más cerca;
- regla 3 fallada o `unknown` (la provincia no se pudo contrastar con el canal);
- regla 4 **fallada**: el canal tiene historial sucio;
- la propuesta trae más filas que el tope.

Esto reemplaza la condición 4 del carril 1 en `docs/spec-motor-validacion.md`
(«las reglas 1, 2, 3, 4 y 6 en `pass`»). Al implementar se agrega allí la sección
«Precisiones de B2b».

### 2. Parámetros

Constantes en `validation/lane1.py`, con referencia a la entrada de G1b. Son
decisiones del operador, no configuración del despliegue.

| Parámetro | Valor propuesto | De dónde sale |
| --- | --- | --- |
| `lane1_max_rows` | 1 | Con 1 fila, 2 errores en 27 sobre el oro; con 2, 10 en 55 |
| `channel_min_history` | 3 | Decisiones individuales para que el canal pueda salir «sucio» |
| `channel_max_edit_rate` | 0,50 | Groupej está en 0,70; es el único canal sucio conocido |
| `min_cell_n` | 5 | El mismo de `persist.MIN_CELL_N` (B1) |
| Meta de error, por anuncio | 10 % | La del carril 0 de texto; no más laxa |
| `n0` | 60 anuncios auditados | 0 errores en 60 dan una cota de 4,9 %; 2 dan 10,1 % |
| Tope de anuncios sin auditar | 30 | Ver «Freno» |

`signal_high_probability` queda sin fijar y las señales externas, vacías: Jev no
entra (no separa: `docs/decisions.md`, 2026-10-05).

### 3. Aplicar: el pre-cálculo guarda las filas

- Interruptor `SAIMC_LANE1_ENABLED`, apagado por defecto. Solo texto.
- Después de guardar la propuesta y de preguntar por el carril 0, el pre-cálculo
  pregunta por el carril 1. Si el motor dice carril 1 y el freno no está puesto,
  en la misma transacción:
  - crea las filas en `prices_history` con `provenance = auto`, con la misma
    fecha, tasa de cambio, formato y variante que resolvería `submit_curation`.
    Para eso el guardado de filas se saca del router a una función compartida;
  - valida y guarda el estado de cada fila (`persist.validate_and_store`);
  - `processing_status = curated`;
  - evento `curated` con `mode = auto` y su `proposal_id`.
- Si la tasa de cambio de una fila en divisa no se puede resolver, el anuncio
  queda `pending`: no se guarda una fila sin convertir.
- Una fila `possible_duplicate` se guarda y se publica igual, con su estado.
- «Completar con IA» no aplica el carril 1: el operador ya está mirando.
- Lo aceptado solo **no cuenta** como decisión individual: no suma al historial
  del canal ni a la evaluación del modo sombra hasta que se audite.
- **Lo que ya está pendiente con propuesta.** Un comando,
  `python -m saimc.validation.lane1` (en seco por defecto; `--execute` aplica),
  recorre los textos pendientes con propuesta `ok` y aplica el mismo camino. No
  gasta API. Se agrega a `docs/comandos.md`.

### 4. Auditoría: `/curation/auditoria`, sección «Aceptados solos»

- **Arranque:** mientras lo auditado no llegue a `n0`, se auditan **todos** los
  anuncios aceptados solos, del más viejo al más nuevo.
- **Desde `n0`:** muestra semanal al azar, estratificada por canal, con semilla
  por semana (molde: la muestra del carril 0). Tamaño propuesto: 30 anuncios, o
  todos si hay menos.
- Cada anuncio muestra su texto y las filas guardadas, con tres acciones:
  - **«Está bien»**: las filas pasan a `provenance = audited`; evento `audited`.
  - **«Corregir»**: abre la edición existente (`/curation/{id}/edit`, con su
    historial de K1). Al guardar, las filas quedan `audited` y el anuncio cuenta
    como error.
  - **«No era un anuncio válido»**: borra sus filas, deja el anuncio `discarded`
    con su motivo y cuenta como error.
- **Qué es un error**, por anuncio: el operador editó o borró algún campo de
  alguna fila, agregó una fila o descartó el anuncio. Es la definición del modo
  sombra.
- **Cota:** sobre lo auditado acumulado, Clopper-Pearson al 95 %, superior e
  inferior, contra la meta. Lo corregido fuera de la muestra se muestra aparte y
  no entra en la cota.
- Una auditoría es una decisión individual: suma al historial de su canal.

### 5. Freno (B2c)

El freno no es un interruptor guardado: se calcula de la base cada vez que el
pre-cálculo o el comando van a aceptar algo. Con el freno puesto, el anuncio
queda `pending`, como hoy.

Se pone cuando pasa cualquiera de estas:

1. **Sin auditar de más.** Hay más de 30 anuncios aceptados solos sin auditar y
   lo auditado no llegó a `n0`. Es lo que hace cumplir la auditoría total del
   arranque: el carril espera al operador.
2. **Evidencia de error, con n chico.** Antes de `n0`, la cota **inferior** del
   error auditado supera la meta.
3. **Desde `n0`**, además, la cota **superior** supera la meta.
4. **Celda divergente.** Si alguna fila del anuncio cae en una celda que diverge
   de sus filas de control (`controls.cell_controls`, C3), ese anuncio no se
   acepta solo. No apaga el carril entero.

Volver a encenderlo después de 2 o 3 es decisión del operador: fija
`SAIMC_LANE1_AUDIT_SINCE` con una fecha, y la cota pasa a contar solo lo auditado
desde ese día. Sin esa variable cuenta todo.

`/control` muestra el carril 1 en rojo con el motivo cuando el freno está puesto.

### 6. Lo que se publica

- `/control` (público) suma las filas por procedencia —a mano, automáticas sin
  auditar y auditadas— y el error auditado con su n y su cota. El número que ve
  un tercero es el auditado, nunca uno estimado.
- `/metodologia` dice que parte de los precios entra sin revisión humana previa,
  con qué condiciones, y que se audita después (`spec.md` §6.4.1).
- El tablero no marca las filas una por una.

### 7. Esquema

No se prevé migración: `provenance` ya admite `auto` y `audited` (0027) y
`curation_events` ya admite `curated`, `audited`, `edited`, `discarded` y el modo
`auto` (0026). Se confirma en el diseño; si hiciera falta, es la 0028 y se avisa
antes de programar.

`raw_ingestions` solo cambia `processing_status`, como en toda curaduría.

## Casos esperados (se escriben antes que el código)

Motor, en la tabla de casos de `tests/` del motor:

1. Texto de una fila, cita verificada, reglas y modelo de acuerdo, canal sin
   historial y celda sin referencias: **carril 1**; la fila sale `unverified`.
2. El mismo, con canal de 3 decisiones y 2 de 3 filas tocadas: carril 2, «el
   canal tiene un historial sucio».
3. El mismo, con canal de 3 decisiones y 1 de 3 filas tocadas: carril 1.
4. El mismo, con canal de 2 decisiones y las 2 filas tocadas: carril 1 (no
   llega al mínimo para salir sucio).
5. El mismo, con celda de 5 referencias y precio fuera de la banda: carril 2;
   la fila sale `anomaly`.
6. El mismo, con reglas y modelo en desacuerdo en el precio: carril 2.
7. Dos filas con tope 1: carril 2, con el motivo del tope.
8. Imagen que cumple todo lo demás: carril 2.
9. Fila con posible duplicado y todo lo demás bien: carril 1, `possible_duplicate`.
10. Canal sin provincia: carril 2 (la regla 3 sigue frenando).

Aplicar:

11. Interruptor apagado: el pre-cálculo escribe igual que hoy, sin filas `auto`.
12. Un anuncio del carril 1 produce las mismas filas, la misma tasa y el mismo
    estado de validación que si el operador lo curara sin tocar nada, salvo
    `provenance` y el modo del evento.
13. Fila en USD sin tasa resoluble: el anuncio queda `pending`, sin filas.
14. Lo aceptado solo no aparece en `channel_history` ni en la evaluación del modo
    sombra; después de auditado, sí.
15. El comando en seco no escribe; con `--execute` acepta lo mismo que listó.

Auditoría y freno:

16. «Está bien» deja las filas `audited` y no suma error.
17. «Corregir» y guardar un precio distinto suma un error y deja las filas
    `audited`.
18. «No era válido» borra las filas, deja el anuncio `discarded` y suma un error.
19. Con 31 aceptados sin auditar y 0 auditados, el siguiente queda `pending`.
20. Con 10 auditados y 4 errores (cota inferior 15,0 %) el freno está puesto; con
    10 y 2 (cota inferior 3,7 %), no.
21. Con 60 auditados y 2 errores (cota superior 10,1 %) el freno está puesto; con
    60 y 1 (7,7 %), no.
22. Con `SAIMC_LANE1_AUDIT_SINCE` posterior a todos los errores, el freno se
    levanta.
23. Un anuncio con una fila en una celda divergente queda `pending`; otro, en una
    celda sana, entra en la misma corrida.
24. `/curation/auditoria` pide operador; `/control` muestra el error auditado con
    su n y su cota, y el motivo del freno.

Las cotas de los casos 20 y 21 se recalculan con `validation.stats` antes de
escribir el test; si no dan lo dicho acá, manda `stats` y se corrige este texto.

## Lo que se sabe y lo que no (2026-10-09, solo lectura)

Con el filtro y los parámetros de arriba:

| | Tope 1 | Tope 2 | Sin tope |
| --- | --- | --- | --- |
| Oro v7: errores / entran | 0 / 18 (cota 15,3 %) | 0 / 21 (13,3 %) | 0 / 22 (12,7 %) |
| Oro v7 sin el freno de canal sucio | 2 / 27 (21,5 %) | 10 / 55 (28,9 %) | 15 / 61 (35,3 %) |
| Pendientes que entrarían hoy, de 497 | 80 (13 canales) | 100 (16) | 123 (16) |

- El 0 de la primera fila es optimista: sale de tres canales viejos, y el tope de
  0,50 se eligió después de ver que los errores eran de Groupej.
- **Los canales nuevos no tienen ninguna decisión del operador.** De los 80 que
  entrarían hoy, 49 son de dos canales (MYP Mayoristas Y Minoristas Alimentos
  Habana, 31; Mipyme Realjo SRL, 18) de los que no se sabe nada. Un canal malo
  publica errores hasta que se junten 3 auditorías suyas.
- Los canales mayoristas entran igual que cualquier otro: el modelo no descarta
  por mayorista (solo por producto fuera del catálogo) y cada fila lleva su
  formato de venta, que es parte de la celda. El carril 1 no los trata aparte.
- A los otros 374 con filas los sigue frenando sobre todo el desacuerdo entre
  reglas y modelo (precio 302, moneda 278, producto 152). Ese freno no se toca
  acá: sin él, sobre el oro, el operador tocó 79 de 151 anuncios.

## Apartamientos de lo escrito

Al implementar se registran en `docs/decisions.md` y en la tabla de
`docs/spec-map.md`:

- **Decisión cerrada 2 de `CLAUDE.md`** («la aceptación sigue siendo manual hasta
  G1b»): el texto nuevo lo edita o aprueba el operador.
- **`spec.md` §6.1**, «Rol del OCR y la extracción asistida»: «el operador
  confirma o corrige» deja de valer para lo que entra por el carril 1.
- **`spec.md` §10.7**: un humano por registro en la Fase 0–1.
- **H0** (`spec.md` §12.5): un anuncio aceptado solo no tiene etiqueta humana y
  no entra en la medición hasta que se audite.

## Qué no entra

- Imágenes en el carril 1.
- Listas de 2 o más filas (queda en el tope; se revisa con lo auditado).
- Tocar el acuerdo entre reglas y modelo, o mejorar el extractor de reglas.
- Jev como señal.
- Marcar en el tablero cada fila automática.
- El carril 0 (G1a) no se toca.

## Costo

Ninguno de API: el carril 1 usa la propuesta que el pre-cálculo ya guardó.

## Orden de entrega

Tres slices, cada uno en su worktree. El interruptor queda apagado hasta que los
tres estén en producción.

| Slice | Qué | Modelo sugerido |
| --- | --- | --- |
| B2b-1 | Motor: qué frena (casos 1–10) y el informe de sombra con el filtro nuevo | Opus |
| B2b-2 | Aplicar: guardado compartido, pre-cálculo y comando (casos 11–15) | Opus |
| B2b-3 | Auditoría, freno, `/control` y `/metodologia` (casos 16–24) | Sonnet; Opus para el freno |

## Preguntas al operador — resueltas el 2026-10-09

El operador aprobó la especificación con las ocho respuestas por defecto.

1. **Tope de filas: 1.** ¿O 2, que suma 20 anuncios hoy con más riesgo?
2. **Meta de error: 10 % por anuncio.**
3. **`n0`: 60 anuncios**, y un máximo de 30 sin auditar durante el arranque.
4. **Canal sucio: 3 decisiones y más de la mitad de las filas tocadas.**
5. **Una fila `possible_duplicate` se publica igual.**
6. **Lo ya pendiente se aplica con un comando a mano**, no solo en la próxima
   corrida del pre-cálculo.
7. **El freno se levanta con `SAIMC_LANE1_AUDIT_SINCE`**, sin tabla nueva.
8. **Groupej queda fuera del carril 1** mientras su historial siga sucio.
