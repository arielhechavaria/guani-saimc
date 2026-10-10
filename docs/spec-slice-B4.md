# Slice B4 — Capa pública: por qué confiamos en cada dato

**Estado: PROPUESTA, sin aprobar.** Nada de esto está programado. El operador
la aprueba (o la corrige) antes del primer commit de código, como se hizo con B1
y B3.

Parte del plan `docs/plan-factor-humano.md`, «B4». Se apoya en `spec.md` §6.5
(las cuatro preguntas: de dónde salió, cuándo, qué procesos atravesó, por qué el
sistema confía), en `spec-slice-B1.md` (columnas `provenance` y
`validation_status`), en `spec-slice-B3.md` sección 5 y en la entrada del
2026-10-10 sobre B3-4 de `docs/decisions.md`.

## Problema

1. **La capa pública afirma algo que dejará de ser cierto.** El tablero dice
   «verificados a mano uno por uno» en dos lugares (subtítulo de la lista y
   «Sobre los datos»). Desde B2b el carril 1 puede publicar filas sin revisión
   previa. Hoy el interruptor está apagado, así que la frase sigue siendo
   verdad; en cuanto se encienda, es falsa.
2. **La procedencia ya está en la base y nadie la muestra.**
   `prices_history.provenance` (`human`, `auto`, `audited`) y
   `validation_status` existen desde la 0027. `RetailObservation.provenance` ya
   viaja hasta las celdas, pero su comentario dice «ninguna vista pública lo
   usa». `validation_status` no viaja.
3. **`/metodologia` no tiene puerta de entrada desde el producto.** Los únicos
   enlaces a ella están en la landing (`/`, dos veces) y en el menú de páginas.
   `/precios` (tablero y Explorar) no enlazan: el pie no se dibuja ahí
   (apartamiento del 2026-09-21) y el panel «Sobre los datos» solo enlaza a
   `/uso-de-datos`. Quien llega al tablero sin pasar por la landing no tiene
   cómo llegar a saber cómo se calcula lo que está mirando.

## Qué se construye

### 1. Qué es «la celda» y qué se cuenta en ella

La celda es **producto × provincia**, la misma de hoy: la tarjeta
`columna`/`celda` del tablero y el nodo de provincia de Explorar. Los
formatos hijos de Explorar **no** llevan el dato (propuesta; ver pregunta 3).

Por celda, con el mismo universo de filas que ya sostiene su mediana y su n
(`RetailObservation`, ventana vigente):

| Qué | De dónde | Cómo se dice |
| --- | --- | --- |
| n | ya existe (`count`) | «N anuncios» (no cambia) |
| a mano | `provenance = human` | «revisados a mano» |
| por el sistema | `provenance = auto` | «aceptados por el sistema» |
| auditados | `provenance = audited` | «aceptados por el sistema y revisados después por una persona» |
| estado de validación | `validation_status` | ver abajo |

`audited` nace solo desde `auto` (`lane1.py`, al confirmar una auditoría), así
que a|b|c suman n. Se muestran **conteos**, no porcentajes: con n chico un
porcentaje aparenta una precisión que no hay, y la proporción se lee igual
desde «9 de 12».

**Estado de validación, en dos grupos y un residuo** (no los siete nombres del
motor, que son vocabulario interno):

- «pasó las seis reglas» = `approved`;
- «con una señal que mirar» = cualquier otro estado (`incomplete`,
  `anomaly`, `inconsistent`, `possible_duplicate`, `untrusted_source`,
  `unverified`);
- «sin evaluar» = `validation_status` nulo (filas anteriores a la 0027 que
  `revalidate` no llenó).

Ningún umbral nuevo (§12.6): no se dice «confiable» ni «poco confiable»; se
cuenta qué hay. Si una celda tiene pocos datos se muestra su n y nada más: no
se inventa un corte ni se oculta la celda.

**Solo conteos.** Ningún canal, emisor, texto ni id de anuncio entra a este
bloque; el identificador de usuario origen no se toca. El enlace «ver anuncio»
que ya existe en el detalle no cambia.

### 2. Cómo se calcula

Una función pura, `trust_of(observations) -> CellTrust`, junto a
`web/explore_view.py` y `watchlist_view.py` (las dos la llaman; ninguna
reimplementa el conteo). `CellTrust` guarda `human`, `auto`, `audited`,
`approved`, `flagged`, `unevaluated` y `n`.

- `RetailObservation` gana `validation_status: str | None = None` (la consulta
  de `compute_retail_observations` ya une `prices_history`; es una columna
  más). Su comentario de `provenance` deja de decir «ninguna vista pública lo
  usa».
- **Sin migración, sin columna nueva, sin tocar el motor.** Lee lo que B1
  persistió.
- **Reusa** `lane1.summary` / `automation_summary` donde ya se usan (el total
  global de «Sobre los datos»); por celda no hay nada previo que reusar, porque
  `provenance_counts` es global, no por celda.

### 3. Dónde se ve, decidido a 390 px

La lista del tablero y la tabla de Explorar ya están densas a 390 px (n,
mediana, cambio, base, «más barata»). Se propone **dos niveles**:

- **En la celda, siempre y de una línea:** `N anuncios · 9 a mano · 3 por el
  sistema`. Si todos son a mano: `N anuncios · todos a mano`. Los auditados se
  cuentan dentro de «por el sistema» en esta línea (el detalle los separa).
  Sin barra, sin color nuevo: texto con los tokens existentes.
- **En el detalle (un `<details>` por producto, ya hay uno de «Los N
  anuncios»):** el desglose completo de la tabla de arriba, con los cuatro
  conteos de procedencia y los tres del estado de validación, por provincia.

En Explorar la línea va en la celda de la provincia (la columna «Anuncios» ya
existe: se le suma el texto bajo el número a 390 px y al lado a 900 px).

Cortes responsive: solo el de 720 px de `design_tokens.css`.

### 4. «Sobre los datos» y el enlace a `/metodologia`

- **Se reescribe la frase falsa** («verificados a mano uno por uno», en el
  subtítulo `ph-sub` y en la primera viñeta del panel) por una que diga lo que
  hay: «N anuncios de los últimos D días: X revisados a mano, Y aceptados por el
  sistema (Z de ellos ya revisados después por una persona).» Con el carril
  apagado, Y = 0 y la frase se reduce a «revisados a mano», que es lo cierto.
- **El enlace «Cómo calculamos los precios →» a `/metodologia` va en el
  subtítulo del tablero y de Explorar, fuera del panel**, en el mismo renglón
  que la frase de procedencia. Se ve a 390 px sin abrir nada. **Además** va
  como acción dentro del panel «Sobre los datos», junto a «Qué es Guani» (el
  panel es donde alguien que busca de dónde sale el dato va a mirar).
  - Costo de dejarlo **solo** adentro: el panel está al final de la página, tras
    la lista; en el teléfono nadie baja hasta ahí para saber cómo se calcula
    algo, así que el enlace existiría y no se descubriría.
  - Costo de dejarlo **solo** arriba: nadie que llegue a «Sobre los datos»
    buscando procedencia encuentra la puerta donde la espera.
  - Propuesta: los dos. Es un solo `<a>` repetido, no una segunda lógica.
- El pie del shell sigue sin dibujarse en `/precios` (apartamiento del
  2026-09-21 intacto).

### 5. Fuera de alcance (se anota, no se hace)

- Las demás apariciones de «uno por uno / a mano» fuera del tablero y
  Explorar: pie del shell (`_public_shell.html`), pie de la home
  (`public_home.html`), landing («Los reviso uno por uno»), `/terminos`
  (texto legal). Se vuelven falsas igual cuando el carril 1 se encienda. No se
  tocan en B4; quedan listadas en la entrada de `decisions.md` como pendiente
  del operador, sobre todo `/terminos`.
- `CLAUDE.md` (decisión 2 y sección «H0») sigue desfasado; lo edita el operador.
- Mapa de calor y vista `/producto/<slug>`: no llevan el dato en B4.
- Cualquier meta, umbral o «puntaje de confianza» publicado: no.

## Casos esperados (se escriben a mano antes que el código)

Sobre filas sintéticas, con la respuesta fijada de antemano:

1. 12 filas de una celda: 9 `human`, 2 `auto`, 1 `audited` → `human=9, auto=2,
   audited=1`; la línea dice «12 anuncios · 9 a mano · 3 por el sistema»; el
   detalle separa «2 sin auditar» y «1 revisado después».
2. 5 filas todas `human` → «5 anuncios · todos a mano»; no aparece «0 por el
   sistema».
3. 1 fila `auto` → «1 anuncio · 1 por el sistema» (singular, sin porcentaje, sin
   corte).
4. Estado: 4 `approved`, 1 `anomaly`, 1 nulo → `approved=4, flagged=1,
   unevaluated=1`; las tres cifras suman n.
5. `human + auto + audited == n` siempre; `approved + flagged + unevaluated == n`
   siempre.
6. Celda sin filas en la ventana → no hay bloque; «sin datos todavía» no cambia.
7. Ninguna salida (HTML ni JSON) de las páginas públicas contiene un id de
   canal, nombre de emisor, `sender_hash` ni texto de anuncio por culpa de este
   bloque (test que recorre el HTML renderizado con filas que tienen emisor y
   canal).
8. Con `automatic_rows = 0`, la frase de «Sobre los datos» no menciona al
   sistema; con `> 0`, la menciona con sus conteos.
9. El HTML de `/precios` y de `/precios?vista=explorar` contiene un enlace a
   `/metodologia` fuera del panel «Sobre los datos», y otro dentro.
10. Tuteo: ningún texto nuevo contiene voseo (`mirá`, `fijate`, `tenés`…) ni las
    cadenas «tasa de automatización» o un porcentaje compuesto.

## Verificación visual

Es interfaz. Orden: prototipo `docs/prototipos/b4-capa-publica.html` a 390 px
primero y 900 px después; luego la página real y el prototipo renderizados lado
a lado a 390 y 900 px con playwright (venv). Aprobado el prototipo antes de
programar la plantilla.

## Costo

Sin migración, sin gasto de API, sin proceso nuevo. Una columna más en una
consulta existente, una función pura, dos plantillas, un par de textos. Modelo
sugerido: Sonnet; no hay decisión de esquema que pida Opus.

## Orden de entrega

Un solo slice, un solo commit: (1) casos esperados; (2) `trust_of` y el campo
nuevo; (3) prototipo y aprobación; (4) plantillas del tablero y Explorar; (5)
textos y enlace; (6) comparación a 390/900 px; (7) `docs/decisions.md`, checklist
de `docs/checklist-pr.md`, commit.

## Apartamientos propuestos — sin escribir

`spec.md` §6.5 pide que todo dato publicado responda «¿por qué el sistema
confía en él?». B4 lo responde con conteos de procedencia y de validación, no
con el `confidence_score` ni el `trust.user_score` del ejemplo de §6.5: ese
puntaje no se publica (no hay meta que lo respalde, §12.6). Se registra en
`docs/decisions.md` al implementar, no antes.

## Preguntas al operador

1. **¿Dos niveles (una línea en la celda + desglose en el detalle) o todo en el
   detalle?** Recomiendo dos niveles: es lo único que cumple «por celda» sin
   abrir nada a 390 px.
2. **¿Conteos o porcentajes?** Recomiendo conteos («9 de 12»): con n chico un
   porcentaje parece una medición que no existe.
3. **¿Los formatos hijos de Explorar llevan el dato?** Recomiendo que no: el
   pedido es por producto × provincia, y un formato con 2 filas lo
   fragmentaría en ruido.
4. **¿El enlace a `/metodologia` arriba y adentro del panel, o solo arriba?**
   Recomiendo los dos (ver sección 4).
5. **Las frases «uno por uno» fuera del tablero (pie, landing, `/terminos`):
   ¿las dejamos para un slice aparte?** Recomiendo que sí, y que `/terminos`
   lo decidas vos antes de encender el carril 1.
