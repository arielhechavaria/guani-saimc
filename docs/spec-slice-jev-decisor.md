# Slice jev-decisor — Jev sale de la sombra en el carril 0 (2026-10-16)

Alcance confirmado por el operador en `docs/analisis-jev-decisor.md` §6:
umbral de descarte P(sí) < 0,2, Rol 1 (segundo juez del carril 0), Rol 2
(descarte propio cuando el asistente no propuso) y Rol 3 (confirmar en el
carril 1) solo si la medición lo respalda. Toca el motor de validación: flujo
de especificación completo (`CLAUDE.md`, «Proceso»).

Contraste con la especificación: `spec.md` §6.1 («Rol del OCR y la extracción
asistida») pone a los asistentes como apoyo de la cola, no como etapas
autónomas. El carril 0 ya es un apartamiento registrado (G1a, 2026-10-03;
`docs/spec-map.md`, «Descarte automático»). Este slice no abre un apartamiento
nuevo: suma un segundo juez a ese mismo carril y lo deja descartar solo en un
caso acotado. El umbral 0,2 está medido, no inventado (§12.6).

## 1. Medición (solo lectura, 2026-10-16)

### 1.1 Huellas y costo de `jev_signals`

| questions_hash | modelo | source | filas | costo (USD) | fechas |
|---|---|---|---|---|---|
| `e89e329658…` | `typesafe-ai/jev` | backfill | 1.418 | 0,0426 | 10-05 |
| `e89e329658…` | `typesafe-ai/jev` | prefill | 2.914 | 0,0876 | 10-05 a 10-16 |

Una sola huella: no hay versiones mezcladas. 4.332 señales, 0,130 USD reales
en 12 días.

### 1.2 Acuerdo con la curaduría humana (`saimc.validation.shadow`)

Corrido en seco con el oro v7 y `min_cell_n 5`, `channel_min_history 3`,
`channel_max_edit_rate 0.5`, `lane1_max_rows 1`.

| | oro v7 (294 textos) | revisiones individuales (260) |
|---|---|---|
| Producto | 142/147 (96,6 %) | 229/251 (91,2 %; cota inf. 87,7 %) |
| Moneda | 140/147 (95,2 %) | 242/251 (96,4 %; cota inf. 93,8 %) |
| Triage P(sí) < 0,2 | 139 descartes, 1 falso (0,7 %; cota 3,4 %) | 1 descarte, 0 falsos |
| Triage P(sí) < 0,5 | 145, 3 falsos (cota 5,3 %) | 8, 7 falsos |

En las revisiones individuales casi no hay descartados: el carril 0 y el lote
ya se los llevan, así que el triage solo se ve sobre lo que quedó para el
operador, y ahí con 0,5 se equivoca 7 de 8. El 0,2 no tiene esa cola.

### 1.3 Rol 2: textos que el asistente no propuso

Textos ya decididos, con señal vigente, sin propuesta guardada (son anteriores
al pre-cálculo; no hay propuestas degradadas en la base hoy):

| decisión del operador | n | Jev descartaría (P(sí) < 0,2) |
|---|---|---|
| descartado | 1.207 | 1.118 |
| curado | 147 | 1 |

1 falso descarte en 1.119 (0,09 %). Cubre el caso que Rol 2 quiere cubrir.

### 1.4 Rol 3: Jev como condición de aceptación del carril 1

Se repreguntó a Jev con las tres preguntas de producción **más provincia**
(catálogo cerrado de 16 provincias más «sin dato») por los 554 textos de los
dos conjuntos: 0,0283 USD, fuera de la base (a un archivo; `jev_signals` no se
tocó y `questions_hash` de producción no cambió). Carril 1 con `lane1.CONFIG`
(tope de 4 filas, el modelo manda sobre las reglas). «Saca» = el anuncio
estaba en el carril 1 y con Jev pasa al 2.

Revisiones individuales (la muestra que cuenta: el diff incluye provincia):
204 en el carril 1, 24 con error, de los cuales 23 son de **envase**, campo
sobre el que Jev no opina.

| condición | umbral | quedan | errores | saca: errores / buenas |
|---|---|---|---|---|
| producto + moneda | cualquiera | 175 | 21 | 3 / 26 |
| producto + moneda | 0,7 | 198 | 22 | 2 / 4 |
| + provincia, «sin dato» no frena | cualquiera | 174 | 21 | 3 / 27 |
| + provincia, «sin dato» frena | cualquiera | 55 | 6 | 18 / 131 |

Oro v7 (el diff no cubre provincia): 123 en el carril 1, 59 con error (38 por
filas borradas: el oro está incompleto en las listas). Producto + moneda saca
5 errores y 0–1 buenas; sumar provincia saca 9 errores y 6 buenas.

Provincia: en el carril 1 de las revisiones, Jev coincide en 62, dice «sin
dato» en 141 y discrepa en 1 (que estaba bien). En el oro discrepa en 9, y en
los 9 **Jev tiene razón**: son ofertas de una tienda de San José, Mayabeque,
del canal «Groupej Morales Surl», cuya provincia fija es La Habana. Ese canal
tiene 10 anuncios (12 filas) que nombran Mayabeque guardados como La Habana,
también los curados a mano. Es un error de dato real que ni la curaduría ni el
motor ven, pero no es un error del carril 1 según su definición (el operador
lo dejó igual).

**Conclusión: Rol 3 no se enciende.** Exigir el acuerdo de Jev baja el error
del carril 1 de 11,8 % (24/204) a 12,0 % (21/175) perdiendo 26 anuncios buenos;
con umbral 0,7, a 11,1 % perdiendo 4. Exigir provincia de forma estricta deja
el carril en 55. Los errores reales del carril son de envase y precio, donde
Jev no pregunta. `lane1.CONFIG.signal_high_probability` sigue en `None`.

**La pregunta de provincia no entra a producción.** Se agregó para medir. Como
condición no aporta, y agregarla cambia `questions_hash`: las 4.332 señales
dejan de ser vigentes y el informe de sombra queda en n = 0 hasta un relleno
de ~0,16 USD. Lo que sí encontró (el canal de Mayabeque) se registra como
hallazgo para un slice aparte.

## 2. Qué cambia

### 2.1 Motor (puro: `validation/types.py`, `validation/engine.py`)

- `AdContext.triage_probability: float | None = None`. P(sí) de un juez
  externo del anuncio entero: «ofrece un piloto con precio». `None` = sin
  señal. Un valor que no sea un número finito entre 0 y 1 (incluido `bool`)
  vale `None`. El motor no sabe que es Jev; no importa `jev_signal`.
- `decide_lane_0(ctx, *, external_discard_below: float | None = None)`.
  Con `None`, el comportamiento es **exactamente el de hoy** (modo sombra).
  Con un umbral `t`:

| asistente | P(sí) | reglas ven piloto (dígito + nombre) | carril |
|---|---|---|---|
| descarta (`off_focus`/`no_data`), ok | `< t` | no | **0** (los dos jueces) |
| descarta, ok | `>= t` | — | **2**: «el modelo descarta, pero Jev no (P(sí) = x)» |
| descarta, ok | sin señal | no | 0, como hoy (sin segundo juez no se endurece) |
| descarta, ok | — | sí | 2, como hoy |
| sin propuesta, degradada o con falla | `< t` | no | **0**: «el asistente no propuso y Jev descarta (P(sí) = x)» |
| sin propuesta, degradada o con falla | `>= t` o sin señal | — | 2, como hoy |
| sin propuesta, degradada o con falla | `< t` | sí | 2 |
| propone filas (no descarta) | cualquiera | — | 2: Jev nunca descarta por encima de un asistente que propone |
| descarta sin motivo válido | cualquiera | — | 2, como hoy |

  El límite es estricto: P(sí) = 0,2 no descarta.
- `validate_ad` no cambia: el carril 1 no lee el triage (Rol 3 apagado).

Por qué «sin señal» cae a lo de hoy en Rol 1: un fallo de Jev nunca le cuesta
nada al resto (`jev_signal`, docstring). Exigir la señal mandaría a la cola
todo lo que hoy se descarta solo cada vez que Jev esté caído o sin cupo.

### 2.2 Carril 0 (con base: `validation/lane0.py`)

- `JEV_DISCARD_BELOW = 0.2`: decisión del operador del 2026-10-16, en código
  como `TARGETS` (no es configuración del despliegue).
- Interruptor `SAIMC_LANE0_JEV_ENABLED` (`Settings.lane0_jev_enabled`, por
  defecto `false`). Apagado, el carril 0 es el de hoy y Jev sigue en sombra.
  Se vuelve a la sombra cambiando el `.env`, sin desplegar.
- `apply_lane_0` (y `run_pending`), con el interruptor encendido y para
  **textos**: lee la señal **vigente** (huella de hoy) del anuncio y pasa su
  P(sí) de triage al motor con `JEV_DISCARD_BELOW`. Una señal de otra huella
  o un triage ilegible es «sin señal». Imágenes: nunca hay señal (Jev no las
  ve; C2).
- Descarte por Rol 2: el motivo (`discard_reason`) lo pone una regla, porque
  Jev no distingue: con algún dígito en el texto, `off_focus`; sin ninguno,
  `no_data` (sin dígito no hay precio). La revisión en `llm_reviews` se guarda
  contra la propuesta degradada, como cualquier descarte automático.
- El evento `discarded/auto` lleva en `detail`: `{"jev_p": x}` cuando hubo
  señal y se usó, y además `"decider": "jev"` en los de Rol 2.
- `run_pending`: para Rol 2 toma la última propuesta aunque esté degradada,
  si no hay ninguna ok. Un pendiente **sin ninguna propuesta guardada** sigue
  fuera: `llm_reviews` exige una propuesta.

### 2.3 Auditoría y freno

- Los descartes por Rol 2 son textos y entran a la muestra semanal y a la cota
  de texto como hoy (meta 10 %).
- `lane0.jev_bound(session)`: la misma cota restringida a los auditados cuyo
  evento dice `"decider": "jev"`. La auditoría (`/curation/audit`) la muestra
  en una línea propia, con el interruptor que la apaga. Rol 1 no necesita cota
  propia: solo puede quitar descartes, nunca agregar uno.
- Superar la meta avisa; apagar lo hace el operador (como el resto del carril 0).

### 2.4 Lo que no cambia

- `questions_hash` y las preguntas de producción (§1.4).
- `lane1.CONFIG`: `signal_high_probability=None`; el comentario cita esta
  medición.
- La anotación `jev` en `validation_detail` y el informe de sombra.

## 3. Casos (escritos antes del código)

Motor, en `tests/test_validation_engine.py` (sección «jev-decisor»), con
textos a mano y la respuesta decidida antes de correr nada:

1. Umbral `None`: un descarte del modelo con P(sí) = 0,05 da lo mismo que hoy,
   y un anuncio sin propuesta con P(sí) = 0,01 sigue en el carril 2.
2. Rol 1: «Vendo iPhone 13, 450 USD», el modelo descarta `off_focus`, P(sí)
   0,05 → carril 0.
3. Rol 1: mismo texto, P(sí) 0,35 → carril 2, motivo que nombra a Jev.
4. Límite: P(sí) = 0,2 → carril 2.
5. Rol 1 sin señal → carril 0 (lo de hoy).
6. Rol 1 con P(sí) 0,05 pero «Arroz 320 la libra» → carril 2.
7. Rol 2: sin propuesta y propuesta degradada, «Vendo iPhone 13, 450 USD»,
   P(sí) 0,1 → carril 0.
8. Rol 2 con piloto en el texto («Arroz 320 la libra») → carril 2.
9. Rol 2 con P(sí) 0,5 o sin señal → carril 2.
10. El modelo propone filas y P(sí) 0,01 → carril 2.
11. P(sí) mal formada (1,5; NaN; `True`; -0,1) vale «sin señal».
12. Imagen sin señal, el modelo descarta → carril 0 (lo de hoy).

Carril 0 con base, en `tests/test_lane0.py`: interruptor apagado no lee a
Jev; Rol 1 con P(sí) 0,4 no descarta; Rol 2 con propuesta degradada y P(sí)
0,1 descarta con `off_focus` o `no_data` según el dígito y deja
`decider: jev`; una señal de otra huella no cuenta; `jev_bound` cuenta solo
los de Rol 2.
