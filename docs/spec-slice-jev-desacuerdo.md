# Slice jev-desacuerdo — Jev arbitra cuando el modelo descarta y las reglas ven un piloto

Estado: aprobada por el operador el 2026-10-19 (propuesta del asistente, autorizada
con «sí autorizo lo de Jev»). Toca el motor de validación: los casos se escriben
antes que el código.

## Contexto

El carril 0 descarta solo cuando el modelo descarta por `off_focus` o `no_data` **y**
las reglas no ven ningún producto piloto en un texto con algún dígito
(`engine._lane_0_reasons`). El slice jev-decisor (`docs/spec-slice-jev-decisor.md`)
sumó a Jev como segundo juez, pero solo para endurecer (P(sí) ≥ 0,2 frena) y para
descartar cuando el asistente no propuso; la regla del piloto en el texto quedó
intacta.

Resultado: el **desacuerdo** (el modelo descarta, las reglas ven un piloto con
dígito) manda todo a la cola. Al 2026-10-19 son 87 de los 241 textos pendientes con
propuesta, el mayor freno del carril 1 y del carril 0. Los 87 tienen señal de Jev
guardada; con P(sí) menor que 0,2 caen 44 (menor que 0,1, 18; menor que 0,05, ninguno).

`scripts/jev_arbitro_eval.py` no pudo medir el error de esta regla: no hay una sola
decisión humana sobre un desacuerdo («sin muestra»). Por eso el descarte nace
**apagado**, con su propia auditoría y su propia cota.

## La regla

Con `SAIMC_LANE0_JEV_ARBITER_ENABLED=true` (y `SAIMC_LANE0_JEV_ENABLED=true`), un
anuncio de texto va al carril 0 si:

1. el modelo propuso (sin degradar, sin falla) y descarta por `off_focus` o `no_data`;
2. el **único** motivo que lo frena es «las reglas ven un producto piloto en el
   texto»; y
3. la señal de Jev vigente da P(sí) **menor** que `JEV_ARBITER_BELOW = 0.2`
   (estricto: 0,20 no descarta).

Sin señal, con P(sí) fuera de [0, 1], no finita o booleana, el anuncio sigue a la
cola (un juez caído no descarta). Solo textos: Jev no ve imágenes.

- Lo que se escribe es lo mismo que cualquier descarte del carril 0: estado
  `discarded`, el `discard_reason` del modelo, la revisión en `llm_reviews` y un
  evento `mode = auto`. Un descarte no crea ninguna fila de precio.
- El evento lleva `detail = {"jev_p": p, "decider": "jev_arbiter"}`. Ese
  `decider` distingue estos descartes de los de Jev solo (`"jev"`, Rol 2).
- `decide_lane_0` recibe un parámetro nuevo, `arbiter_below: float | None`
  (`None` = lo de hoy). Quien lo llama pasa `JEV_ARBITER_BELOW` solo si ambos
  interruptores están encendidos y hay hash de preguntas de Jev.
- Apagado por defecto: el comportamiento, el informe de sombra y todo lo ya
  guardado siguen como hoy. `ENGINE_VERSION`, las seis reglas y el estado de las
  filas no cambian.

## Casos esperados (escritos antes que el código)

`arbiter_below = 0.2` y `external_discard_below = 0.2` salvo que se diga otra cosa.
Texto con dígito y producto piloto: «Pollo agotado, escribe al 5555 1234».

| # | Anuncio | Carril | Motivo |
|---|---------|--------|--------|
| J1 | modelo descarta `no_data`, las reglas ven piloto, P(sí) = 0,05 | 0 | descarta y dice que Jev arbitra con P(sí) = 0,05 |
| J2 | igual, P(sí) = 0,20 | 2 | sigue el motivo del piloto y «Jev no» |
| J3 | igual, P(sí) = 0,19 | 0 | descarta |
| J4 | igual, sin señal de Jev | 2 | el piloto en el texto |
| J5 | modelo descarta sin motivo válido (`discard_reason` nulo), P(sí) = 0,01 | 2 | el motivo no es off_focus ni no_data |
| J6 | el modelo propone filas (no descarta), P(sí) = 0,01 | 2 | el modelo no descarta |
| J7 | modelo descarta, las reglas NO ven piloto, P(sí) = 0,50 | 2 | «el modelo descarta, pero Jev no» (lo de hoy) |
| J8 | J1 con `arbiter_below = None` | 2 | el piloto en el texto (lo de hoy) |
| J9 | propuesta degradada, las reglas ven piloto, P(sí) = 0,01 | 2 | el Rol 2 sigue frenando por el piloto |
| J10 | J1 con P(sí) = NaN, 1,5, −0,1 o `True` | 2 | sin señal válida |
| J11 | J1 con un texto sin ningún dígito | 0 | no hay desacuerdo: lo descarta el carril 0 de siempre (no pasa por el árbitro) |
| J12 | J1 aplicado con `apply_lane_0` | 0 | `discarded`, motivo del modelo, evento `auto` con `decider = jev_arbiter` y `jev_p` |
| J13 | J1 con el interruptor del árbitro apagado, `apply_lane_0` | — | devuelve `False` y no escribe nada |

## Auditoría y cota propias

- `jev_bound` hoy cuenta solo `decider = jev`. Se generaliza: `arbiter_bound` hace lo
  mismo para `decider = jev_arbiter`, sobre los descartes de esa clase que ya están
  auditados (rescatados sobre auditados), con la cota superior al 95 % y la meta
  del texto (10 %).
- Estos descartes **entran también** a la cota de texto de todo el carril 0 y a la
  muestra semanal. Además, mientras la cota propia tenga menos de 30 auditados, el
  primer tramo se audita completo (ver «Despliegue»).
- Se muestra donde hoy se muestra `jev_bound` (`/curation/auditoria`, `/control`,
  `/metodologia`) con su propio rótulo y su interruptor
  (`SAIMC_LANE0_JEV_ARBITER_ENABLED`).
- Si la cota superior supera la meta, el indicador pasa a ámbar como los demás;
  apagar el árbitro sigue siendo una decisión del operador.

## Despliegue

1. Se mergea apagado. Nada cambia hasta que el operador lo encienda.
2. `python -m saimc.validation.lane0` en seco imprime cuántos descartaría y cuántos
   de ellos por el árbitro. Esperado el 2026-10-19: unos 44.
3. El operador lo enciende en el `.env`, corre `lane0 --execute`, y audita **todos**
   los descartes del árbitro del primer tramo en `/curation/auditoria` (no solo la
   muestra de 30) antes de dejarlo prendido. Con 44 auditados y 0 rescatados la cota
   superior al 95 % queda en 6,6 %, bajo la meta.

## Qué no cambia

- Las seis reglas, el estado de las filas, el puntaje, `validate_stored_row` y
  `ENGINE_VERSION`.
- El carril 1, el freno por error auditado y las imágenes.
- El Rol 1 y el Rol 2 de Jev (endurecer; descartar cuando el asistente no propuso).
- `JEV_DISCARD_BELOW` (0,2) y el hash de preguntas de la señal.

## Riesgo asumido

Un descarte equivocado pierde cobertura (un anuncio bueno no llega a la cola ni a la
capa pública), no crea un precio erróneo. La cota se mide con la auditoría desde el
primer descarte. Sin muestra humana previa, el 0,2 es el umbral que ya usa el carril
0 para Jev y no está calibrado para este caso: si la auditoría rescata alguno, se
baja a 0,1 (18 anuncios hoy) en lugar de apagar el árbitro.
