# Slice B2a — Carril 0 encendido: descarte automático con auditoría

Parte del plan `docs/plan-factor-humano.md`. Toca esquema (migración 0026): esta
especificación se aprueba antes de programar. Parte de lo que fijó G1a
(`docs/decisions.md`, 2026-10-03): carril 0 para texto e imagen, metas de falsos
descartes de 10% y 20%, auditoría semanal de 30 textos y 15 imágenes.

## Problema

1. El carril 0 del motor (`saimc.validation`) existe pero nadie lo aplica: todo
   descarte lo sigue haciendo el operador, de a uno o en lote.
2. El pre-cálculo solo le manda al modelo los textos que nombran un piloto
   (`prefill.select_candidates`). El carril 0 exige que las reglas *no* vean
   ningún piloto, así que el carril 0 de texto no se dispararía nunca. El
   operador decidió (2026-10-03) mandar también los textos sin foco al modelo.
3. No hay forma de auditar un descarte automático ni de devolverlo a la cola.

## Qué se construye

### Interruptores

Dos variables de entorno, apagadas por defecto, en `Settings`:
`SAIMC_LANE0_TEXT_ENABLED` y `SAIMC_LANE0_IMAGE_ENABLED`. El plan decía un solo
`SAIMC_LANE0_ENABLED`; son dos porque G1a fijó una meta por tipo y cada tipo se
tiene que poder apagar solo.

### Pre-cálculo

- Con `SAIMC_LANE0_TEXT_ENABLED` encendido, `select_candidates` también toma los
  textos pendientes **sin** piloto. Apagado, se comporta como hoy: no se gasta
  API en textos que nada va a descartar solo.
- Después de `save_proposal` y antes del `commit` del anuncio, si el interruptor
  de su tipo está encendido, el pre-cálculo pregunta al motor por el carril 0.
- Si el motor da carril 0, en la misma transacción:
  - `processing_status = discarded` y `discard_reason` = el motivo del modelo
    (`off_focus` o `no_data`);
  - `llm_reviews` con `outcome = discarded`, igual que el lote de imágenes, para
    que el anuncio quede fuera de H0 como hoy;
  - evento en `curation_events`: `action = discarded`, `mode = auto`,
    `proposal_id`, `decided_at` = ahora, `shown_at` nulo.
- Para las imágenes, el contexto del motor recibe el mismo texto de OCR redactado
  que vio el modelo: no se corre el OCR dos veces.
- El resumen del pre-cálculo informa cuántos se descartaron solos, por tipo.
- «Completar con IA» (`POST /curation/{id}/assist`) **no** aplica el carril 0: el
  operador ya está mirando el anuncio.

### El motor

El carril 0 no usa ningún parámetro de `ValidationConfig`, pero hoy
`validate_ad` lo niega si `config` es `None`. Se agrega una función pública del
motor que decide **solo** el carril 0 con los mismos predicados de `validate_ad`
(propuesta presente, sin degradar, el modelo descarta por `off_focus` o `no_data`,
las reglas no ven ningún piloto). El interruptor por tipo hace de configuración.
`validate_ad` no cambia de comportamiento.

### Auditoría: `/curation/auditoria`

- **Población:** los eventos `discarded` + `auto` de una semana cerrada (lunes a
  domingo, por `decided_at`, la misma semana que el panel de A2d).
- **Muestra:** al azar, 30 textos y 15 imágenes, o todos si hay menos. La semilla
  sale de la semana (`lane0-audit-2026-W40`), así la misma semana da siempre la
  misma muestra. Por defecto se abre la última semana cerrada, con selector de
  semanas anteriores.
- Cada anuncio muestra su texto o su miniatura y el motivo del modelo, con dos
  acciones: **«Bien descartado»** y **«Rescatar»**. Molde: el lote de imágenes,
  a 390 px primero.
- **Rescatar** devuelve el anuncio a `pending`, borra su `discard_reason` y escribe
  el evento `action = rescued`. El anuncio vuelve a la cola con su propuesta. El
  pre-cálculo no lo vuelve a tomar, porque ya tiene propuesta `ok`.
- **«Bien descartado»** escribe el evento `action = audited` y no cambia nada más.
- Ambos eventos van con `mode = individual`: los decide una persona.
- **Cota, por tipo:** sobre lo auditado acumulado de todas las semanas, con
  k = rescatados y n = rescatados + bien descartados, la cota superior de
  Clopper-Pearson al 95% contra su meta. Lo rescatado fuera de la muestra se
  muestra aparte y no entra en la cota, porque no es al azar.
- **Si la cota supera la meta**, la pantalla lo marca en rojo y dice qué
  interruptor apagar. No apaga nada sola: el freno automático es de B2c y es para
  el carril 1.

### `/control`

- El panel «Horas y automatización» ya cuenta `mode != individual` como
  automatizado, así que los descartes automáticos entran solos.
- Se suma una línea con los descartes automáticos de la semana y los rescatados,
  por tipo. La cota **no** se muestra en `/control`, que es público: queda en
  `/curation/auditoria`, que pide operador.

### Esquema: migración 0026

- `ck_curation_events_mode` suma `auto`, como ya anticipaba la 0025.
- `ck_curation_events_action` suma `rescued` y `audited`.
- Mismo patrón que la 0025 (`batch_alter_table`), con su test de migración, y el
  CHECK del modelo actualizado.
- `raw_ingestions`: se cambian `processing_status` y `discard_reason`, igual que
  en todo descarte existente (`decisions.md`, metadato de procesamiento). Nada más
  de la fila.

### Constantes

Las metas (10% y 20%) y el tamaño de la muestra (30 y 15) son decisiones del
operador, no configuración del despliegue: van como constantes en código, con
referencia a la entrada de G1a. Clopper-Pearson pasa de `shadow.py` a un módulo
puro, porque `shadow` importa la base y `argparse`.

## Casos esperados (se escriben antes que el código)

1. Interruptores apagados: el pre-cálculo selecciona y escribe igual que hoy, sin
   ningún evento `auto`.
2. Texto encendido: un texto pendiente sin piloto entra en los candidatos;
   apagado, no entra.
3. El modelo descarta un texto por `off_focus` y las reglas no ven piloto: queda
   `discarded`, con su `llm_reviews` y el evento `discarded/auto`.
4. El modelo descarta un texto que nombra un piloto: sigue `pending` y sin evento.
5. El modelo descarta por un motivo que no es `off_focus` ni `no_data`: sigue
   `pending`.
6. Propuesta degradada o fallida: sigue `pending`.
7. Imagen con su interruptor apagado y texto encendido: la imagen no se descarta
   sola, aunque el motor dé carril 0.
8. La muestra de una semana es la misma en dos llamadas; tiene a lo sumo 30 textos
   y 15 imágenes; si hay menos, están todos.
9. Un evento `auto` de la semana en curso no entra en ninguna muestra.
10. Rescatar deja el anuncio `pending`, sin `discard_reason`, con evento
    `rescued`; el pre-cálculo siguiente no lo vuelve a tomar.
11. Rescatar algo que no está descartado, o no está en la muestra, no cambia nada.
12. La cota: 0 rescatados en 30 textos auditados da 9,5%, debajo de 10%; 1 en 15
    imágenes da 27,9%, por encima de 20%, y la pantalla lo marca.
13. Un anuncio rescatado y después descartado de a uno cuenta como decisión
    individual en el historial del canal y en el modo sombra.
14. La migración 0026 sube y baja, y el CHECK rechaza un `mode` desconocido.
15. `/curation/auditoria` pide operador; `/control` no muestra la cota.

## Qué no entra

- Aplicar el carril 0 a lo que ya está pendiente con propuesta: lo hace la próxima
  corrida del pre-cálculo solo sobre anuncios sin propuesta `ok`. Lo viejo sigue en
  la cola o en sus lotes.
- Temporizador del pre-cálculo (C1). Sigue siendo manual.
- Freno automático (B2c, carril 1).

## Costo

Cada texto sin foco que pasa por el modelo cuesta $0,0015–0,0029 (estimación del
plan). Lo que haya pendiente sin foco se procesa una vez; el pre-cálculo ya tiene
`--max-cost` y `--limit`. Antes de la primera corrida con texto encendido se
estima el monto con el pre-cálculo en seco (su modo por defecto) y lo aprueba el
operador.

## Preguntas al operador — resueltas el 2026-10-03

El operador aprobó la especificación con las cinco respuestas por defecto.

1. Dos interruptores (`SAIMC_LANE0_TEXT_ENABLED` y `SAIMC_LANE0_IMAGE_ENABLED`) en
   vez de uno.
2. La muestra sale de la última semana cerrada, no de la semana en curso.
3. La cota se acumula entre semanas y solo cuenta lo auditado de la muestra.
4. Superar la meta solo avisa; apagar el tipo lo hace el operador.
5. La cota no se publica en `/control`.
