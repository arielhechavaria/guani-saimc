# Jev como decisor — análisis de pros y contras (2026-10-16)

El operador decidió sacar a Jev del modo sombra. Este documento argumenta esa
decisión con los datos que hay y propone un **alcance**. La decisión todavía
no está en `docs/decisions.md`: falta que el operador confirme el alcance
(sección 5). Es una propuesta, no una implementación.

Nota: el operador escribió «427 señales»; la base local tiene **4257** filas en
`jev_signals` (reporte de estado, §4). Se usa 4257, a verificar contra
producción.

## 1. Qué se decide

Hoy Jev contesta tres preguntas cerradas por anuncio (triage sí/no, producto,
moneda) y se guarda en `jev_signals`, pero **el motor no lo lee**
(`validation/jev_signal.py:23-25`; `lane1.py:100`: «Jev no separa: sin
umbral»). El mecanismo para leerlo ya existe en el motor
(`engine.py:325-345`, `ExternalSignal`) y está apagado con
`signal_high_probability=None`. «Pasar a decisor» significa encender ese
mecanismo con un umbral, y/o dejar que Jev descarte en el carril 0.

## 2. Lo que dicen los datos

| Medición | Resultado | Fuente |
|---|---|---|
| Triage (A6, 1.409 textos) | 95,6 % de acierto, Brier 0,042; referencia trivial 88,9 % | `decisions.md` 2026-10-04 |
| Triage como descarte, umbral 0,2 | descartaría el 92,5 % de los descartados; **0/157 curados perdidos** (cota 95 % de 1,9 %) | ídem |
| Oro v7, P(sí) < 0,1 | 0/109 falsos descartes (cota 2,7 %) | `decisions.md` 2026-10-05 |
| Oro v7, P(sí) < 0,2 | 1 falso descarte en 139 (0,7 %; cota 3,4 %) | ídem |
| Lado alto (aceptar) | **sobreconfiado**: en el tramo 0,9–1,0 declara 0,95 y acierta 78,5 % | A6 |
| Producto / moneda | 96,8 % / 94,3 % (muestra de 157) | A6 |
| Carril 1, errores | el carril se equivoca en 4 de 9 en v7; **Jev no frena ninguno** | oro v7 (n = 9) |
| Costo | 0,000017–0,000066 USD por anuncio; tope de 0,08 USD por día | `docs/costos.md` |

Límites de estos datos: las cifras de A6 se midieron con preguntas distintas
de las de producción («las cifras de A6 no se heredan», `decisions.md`
A6b-1); el oro v7 es chico (n = 9 en el carril 1) y no está en
`docs/shadow-runs.jsonl`, cuya única línea es del 2026-10-05 con n = 34/11.
**No hay medición de acuerdo con el humano sobre las 4257 señales actuales.**

## 3. A favor

1. **Descarta bien y barato.** Con P(sí) < 0,1 no descartó ningún anuncio
   curado en el oro v7, y el costo es de centavos al mes (cálculo propio,
   ~0,075 USD con 35 textos por día; **no hay costo real medido**).
2. **Aporta un segundo juez independiente al carril 0**, que hoy depende de la
   propuesta del asistente y de las reglas. Dos decisores distintos que
   coinciden bajan el riesgo de falso descarte.
3. **Ya corre solo**; encender el uso no suma infraestructura.
4. **Reduce la cola**: los descartes son el 68 % de lo que el operador procesa
   a mano (`CLAUDE.md`, decisión 2).

## 4. En contra

1. **No sirve para aceptar.** Del lado alto está sobreconfiado y no frenó
   ninguno de los 4 errores del carril 1 en v7. Usarlo para aceptar sería
   confiar en la parte peor calibrada.
2. **El aporte marginal medido es chico:** de 139 textos que descartaría, 131
   ya los descarta el motor; suma 8 sobre 294, con 1 falso descarte.
3. **No crea filas «validadas».** Una señal de Jev no cambia `provenance` ni
   produce `approved`; el motor no la lee. Ponerlo de decisor **no resuelve**
   la definición de «validado» (T0).
4. **Riesgo de versiones mezcladas.** Si cambian las instrucciones o la lista
   de productos, cambia `questions_hash` y se agregan filas nuevas; las
   viejas quedan. Además el alias del modelo no cambia aunque el proveedor
   cambie los pesos, y la huella no lo detecta (inferido del código; sin
   evidencia de que haya pasado). `source` mezcla `prefill` y `backfill`.
5. **Proveedor en acceso anticipado**, sin español documentado ni pesos
   autoalojables (`decisions.md`, 2026-09-29): dependencia de un tercero para
   una decisión que borra anuncios.
6. **Un descarte automático equivocado es silencioso**: el anuncio no vuelve
   a verse salvo por la auditoría semanal (30 textos, 15 imágenes).
7. **Es un cambio de decisión previa**: `decisions.md` (2026-10-05) dice «Jev
   no entra» y `lane1.py` lo fija. Requiere entrada nueva y flujo completo
   (toca el motor).

## 5. Recomendación de alcance (a confirmar por el operador)

**Sí a sacarlo de la sombra, pero con un rol acotado por lo que los datos
respaldan:**

1. **Rol 1 — segundo juez de descarte (carril 0):** un anuncio se descarta
   automáticamente solo si el asistente propone `discard` **y** Jev da
   P(sí) < 0,1 (umbral del 0/109) **y** las reglas no ven producto piloto.
   Hoy basta el asistente más las reglas; sumar a Jev solo endurece.
2. **Rol 2 — Jev solo puede descartar por su cuenta con P(sí) < 0,1** los
   anuncios que el asistente no pudo proponer (por ejemplo, el asistente
   falló o está degradado). Ahí sí agrega cobertura. *Decisión del
   operador: ¿se quiere este rol?*
3. **Rol 3 — aceptar: no.** Hasta tener una medición propia y calibrada
   sobre las señales vigentes, Jev no acepta ni sube la confianza de una
   fila. Sigue anotado en `validation_detail`.
4. **Condiciones previas** (T3): contar huellas distintas, medir el costo real
   y el acuerdo con la curaduría humana sobre las señales vigentes; usar
   solo la señal de la huella vigente; **no reinterpretar con efecto
   retroactivo** las filas ya decididas (las 4257 señales viejas son
   anotación; solo cuentan las vigentes sobre anuncios pendientes).
5. **Freno y auditoría:** la auditoría del carril 0 ya mide falsos descartes
   (metas 10 % y 20 %); el freno debe contar los descartes por Jev por
   separado para poder apagarlo si su error sube.
6. **Interruptor:** un parámetro de configuración para volver al modo sombra
   sin desplegar.

**Si el operador quiere ir más lejos** (Jev acepta o decide filas del carril
1) hace falta primero medir su calibración del lado alto con las señales
actuales; hoy el dato dice lo contrario.

## 6. Qué hay que decidir

1. ¿Se confirma el alcance de la sección 5 (descarte sí, aceptación no por
   ahora)? Si es otro, hay que decirlo antes de especificar.
2. ¿Se acepta el umbral de partida P(sí) < 0,1, o se prefiere 0,2? (0,1: 0 falsos
   en 109; 0,2: 1 en 139, más cobertura).
