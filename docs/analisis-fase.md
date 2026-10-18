# Análisis de fase — criterios de salida de la Fase 0 (2026-10-18)

Tarea T14 de `docs/plan-bloque-mvp.md`. Solo lectura: no cambió ningún dato ni
se registró ninguna corrida. **No declara la transición**: es del operador.

Fuentes: SELECT en transacción de solo lectura sobre la base de producción (el
VPS, 2026-10-18 07:55 UTC); `python -m saimc.extraction.measure --no-record` y
`python -m saimc.extraction.reference_set` en seco; el journal de
`saimc.service` para las visitas. Contra `spec.md` §3, §9, §9.0 y §12.5, con los
apartamientos de `docs/spec-map.md`.

## 0. Resumen

| Pregunta | Respuesta corta |
|---|---|
| ¿Se cumple el criterio de salida vinculante de §9? | **Sí, con la salvedad de B3-2**: H0 está respondida con un número (38,8 %, n = 220), sin llegar a 300 |
| ¿500 registros validados? | **No**: 226 filas `approved` (397 si una fila auditada contara como verificada) |
| ¿Métrica de la Fase 0 (B3) dentro de metas? | **Sí**: las tres cotas de error auditado están bajo su meta |
| ¿Se puede hacer una corrida «oficial» (`is_official`)? | **No por construcción**: el conjunto está congelado en 220 y ya no crece |
| ¿Hay algo que bloquee abrir el sitio? | **T1**: 266 filas en USD con tasa de relleno, 29 de ellas `approved` |

## 1. Correcciones al reporte del 2026-10-16

- **«Validado» ya se decidió y se midió**: `approved` (`decisions.md`,
  2026-10-16, «Fila validada = `approved`…»; commit 09c0b27). Ya no hay 0
  filas `approved`: hay **226**.
- **El conjunto de referencia no tiene 436 miembros**: `reference_set_members`
  tiene 436 filas porque guarda **dos versiones** (v1 con 216 anuncios, v2 con
  220). H0 se mide contra la v2: 220 anuncios, 353 filas.
- **T1 no toca H0.** El plan (T14) supone que con tasas de relleno «las cifras
  de precio de H0 no son confiables». No es así: la precisión de precio compara
  `price` y `currency` contra la etiqueta (`extraction/measure.py`, función de
  comparación de filas), nunca `price_cup`. T1 sí afecta a los agregados
  públicos y, por las reglas 2 y 6, al estado de validación.
- La puerta del sitio (`SAIMC_SITE_GATE_ENABLED=true`, decisión del
  2026-10-13) explica la caída de visitas desde el 2026-10-14 (sección 4).

## 2. Fase 0 — alcance (`spec.md` §9, «Fase 0 — Fundación del Sistema»)

| Ítem | Estado | Dato |
|---|---|---|
| Captura: bandeja + cola, sin automatización | Apartado (WAHA, 2026-08-15) | Decisión cerrada 2 |
| 3–5 productos bandera | Cumple | 5 piloto: Aceite, Arroz, Azúcar blanca, Picadillo, Pollo |
| 2 provincias | Cumple | La Habana y Santiago de Cuba; ninguna otra tiene filas |
| Mapa de calor y tarjetas de producto | Cumple | Capa pública construida (detrás de la puerta) |
| Reglas 1, 2, 4 y 5 | Cumple de más | Seis reglas en el motor |
| Motor en modo «entrenamiento» (propone, no decide) | Apartado | Carril 0 descarta solo (G1a), carril 1 acepta solo (G1b) |
| Conjunto de referencia de 300+ | No cumple, apartado | 220 congelados (B3-2); `spec-map.md`, «Tamaño del conjunto de referencia» |

## 3. Fase 0 — resultado esperado y criterio de salida

### 3.1 «500 registros validados de 3–5 productos en 2 provincias»

`prices_history`, 1145 filas, por estado y procedencia:

| Estado | human | auto | audited | Total |
|---|---:|---:|---:|---:|
| `approved` | 226 | 0 | 0 | **226** |
| `unverified` | 93 | 278 | 193 | 564 |
| `possible_duplicate` | 112 | 92 | 83 | 287 |
| `inconsistent` | 13 | 8 | 7 | 28 |
| `untrusted_source` | 1 | 0 | 15 | 16 |
| `anomaly` | 6 | 4 | 4 | 14 |
| `incomplete` | 10 | 0 | 0 | 10 |

`approved` por producto y provincia:

| Producto | La Habana | Santiago | Total |
|---|---:|---:|---:|
| Arroz | 63 | 28 | 91 |
| Pollo | 37 | 20 | 57 |
| Azúcar blanca | 25 | 3 | 28 |
| Aceite | 26 | 0 | 26 |
| Picadillo | 11 | 13 | 24 |
| **Total** | **162** | **64** | **226** |

- **Faltan 274** para 500. Las filas `auto` nunca llegan a `approved` (§6.1,
  §10.7, decisión del 2026-10-16), así que solo suma la curaduría humana.
- **Las seis reglas en `pass` y solo falta «verificada»**: 171 `audited` y
  258 `auto`. Si una fila auditada contara como verificada (pendiente sin
  aprobar en la entrada del 2026-10-16), el total sería **397**. Es una cota:
  no distingue las auditadas con y sin corrección.
- **Ritmo**: 226 `approved` en 10 semanas (22,6 por semana); 74 en las
  últimas 4. A ese paso, 500 llega en **12 a 15 semanas** (estimación lineal,
  no medición). Con las auditadas, faltarían 103.
- **Santiago es la celda débil**: 64 `approved`, Aceite con 0 y Azúcar blanca
  con 3. Solo 4 canales tienen Santiago como provincia (La Habana, 44).
- **Ruido de catálogo**: 10 filas apuntan a productos que no son piloto y
  tienen nombres basura («Precio :», «Paquete de azúcar de», «Azúcar
  blanco»…). Ninguna es `approved`. No es de este slice.

### 3.2 «H0 quedó respondida con un número» (criterio vinculante)

Corrida de hoy sin registrar, conjunto v2 (hash `514f9795…`, sin deriva).
Da los mismos números que la corrida registrada del 2026-10-10 (commit
61657ca): el conjunto está congelado y el extractor por reglas no cambió.

| Métrica (§12.5) | Medido | Umbral mínimo | Objetivo |
|---|---:|---:|---:|
| Producto | 85,3 % | 85 % | > 92 % |
| Precio | 54,4 % | 90 % | > 95 % |
| Provincia | 92,4 % | 80 % | > 90 % |
| Cantidad/unidad | 55,0 % | 70 % | > 85 % |
| **Extracción completa correcta** | **38,8 %** | 70 % | > 85 % |
| Tasa de descarte | 69,4 % | — | < 30 % |

- **El criterio se cumple** en la forma que fijó B3-2: un número sobre el
  conjunto congelado, diciendo que no llega a 300. El corte «debajo de 70 % la
  Fase 1 no arranca» está apartado desde el 2026-08-12 (H0 es métrica de
  eficiencia, no veredicto).
- **`is_official` no puede ser verdadero**: exige 300 anuncios
  (`measure._H0_MINIMUM_ADS`), y el conjunto no crece. El ensayo de
  `reference_set` lo confirma: de 700 curados, 479 nacieron asistidos y 1 no
  tiene filas; entrarían los mismos 220 de hoy.
- Lectura: el extractor por reglas reconoce producto y provincia; falla en
  precio y en envase. Es la razón por la que hoy decide el modelo y no las
  reglas, y coincide con la nota de §12.5 sobre la unidad.

### 3.3 Métrica de la Fase 0 vigente (B3, 2026-10-10)

Desde la semana del primer evento automático (2026-09-28), 4722 anuncios:

| Número | Valor |
|---|---|
| N1 — resuelto sin el operador | 76,7 % |
| N2 — publicado sin el operador | entre 43,7 % y 89,5 % |

| Error auditado | Medido | Cota 95 % | Meta |
|---|---|---:|---:|
| Carril 0, texto | 0 de 60 | 4,9 % | 10 % |
| Carril 0, imagen | 0 de 30 | 9,5 % | 20 % |
| Carril 1, por anuncio (desde 2026-10-15) | 1 de 60 | 7,7 % | 10 % |

Las tres cotas están bajo su meta. La de imagen queda cerca (9,5 contra 20 da
margen; la de carril 1, 7,7 contra 10, menos).

## 4. Fase 1 (`spec.md` §9, «Fase 1 — Lanzamiento Público Inicial»)

La especificación **no define criterios de entrada propios** para la Fase 1:
la única puerta es el criterio de salida de la Fase 0. Lo que sigue son los
contenidos y la métrica de éxito de la Fase 1, medidos para saber desde dónde
arranca.

| Ítem | Estado | Dato |
|---|---|---|
| Dashboard público con mapa de calor | Construido, **cerrado** | Puerta del sitio encendida desde el 2026-10-13 |
| Tarjetas con semáforos | Apartado | Sin verde/rojo (2026-09-21, `spec-map.md`) |
| Ingesta a 10 productos y 5 provincias | No empezado | 5 productos y 2 provincias con filas; techo de ingesta, no meta (§9.0) |
| SAIMC Market Report | No existe | Ninguna referencia en el código |
| **100 usuarios activos diarios** | No medible hoy | Ver abajo |
| Provincias publicadas (§9.0) | 0 de 16 con `is_published` | Coherente: Fase 0–1 es calibración de N, que se fija al cierre de la Fase 1 |

Visitantes únicos por día en la capa pública (`docs/comandos.md`, «Visitantes
únicos del sitio»): entre **15 y 31** del 2026-10-04 al 2026-10-13, y entre
**2 y 4** desde el 2026-10-14, con la puerta. El número antes de la puerta no
filtra robots (`web/analytics.py` no mira el agente) y cuenta una IP como una
persona: no es una línea de base de DAU confiable.

## 5. Propuestas

Cada una es para que decida el operador. Ninguna está aplicada.

### 5.1 Definición de «validado»

**Mantener `approved`** (decidido el 2026-10-16) como la única definición, y
**resolver el pendiente**: que una fila `audited` cuente como verificada.

- **A favor**: la auditoría es el mismo acto que la curaduría —una persona mira
  la fila contra el anuncio y confirma o corrige— y el argumento del sesgo de
  anclaje ya no separa a las dos: desde B3-2 toda la curaduría nace asistida, o
  sea que la fila `human` también es una propuesta del modelo confirmada.
  Suma hasta 171 filas.
- **En contra**: cambia `verified` en el motor de validación. Por `CLAUDE.md`
  eso pide el flujo completo y `revalidate --execute`. Una auditada sigue
  fuera del conjunto de referencia de H0 (2026-10-10, B3-2): no cambia esa
  regla, son cosas distintas.
- **Recomendación**: hacerlo, como slice propio con especificación, solo para
  las auditadas **sin** corrección o **con** corrección ya aplicada (las dos
  dejan la fila verificada por una persona). Las `auto` siguen fuera.

### 5.2 Corrida oficial de H0

**Recomendación: no perseguir `is_official = true`. Registrar una corrida
nueva al declarar la transición.**

- Llegar a 300 exige etiquetar **80 anuncios a ciegas** (sin la propuesta del
  asistente), un modo de curaduría que hoy no se usa. Es trabajo del operador
  para mover un número que ya no gobierna nada (2026-08-12).
- Registrar una corrida (`measure` sin `--no-record`, siguiendo
  `docs/runbook-medir-h0.md`) deja en `docs/h0-runs.jsonl` un registro
  atado al commit del día de la transición y, a diferencia del del 2026-10-10,
  con la métrica de automatización (N1, N2 y las cotas) en el mismo registro.
  Costo: un comando, sin gasto.
- **No hace falta esperar a T1**: H0 no lee `price_cup` (sección 1).
- Alternativa: aceptar la corrida del 2026-10-10 sin registrar otra. Válida,
  pero ese registro no trae la métrica de la Fase 0.

### 5.3 Criterio de transición

Propuesta: **la Fase 0 se da por cerrada cuando se cumplen las cuatro**:

1. **H0 registrada con un número** sobre el conjunto congelado, con su n
   (§9, primera oración, en la forma de B3-2). *Hoy*: se cumple con la del
   2026-10-10; 5.2 propone una nueva.
2. **Métrica de la Fase 0 publicada y con las tres cotas de error bajo su
   meta** (B3). *Hoy*: se cumple.
3. **T1 aplicado**: ninguna fila en divisa con `elTOQUE (cache)`. *Hoy*: 266
   filas, 29 `approved`. Abrir el sitio con esos precios publica un dato
   falso (decisión del 2026-10-16 sobre la tasa).
4. **Después de T1, `revalidate --execute`** y un conteo de `approved` nuevo,
   porque las reglas 2 y 6 miran el precio convertido (`price_cup`).

**Los 500 validados no entran como condición.** §9 los pone como «resultado
esperado», no como criterio de salida; y §9.0 («Cómo se concilia con las
Fases 0 y 1») ya resuelve que se publique con muestra chica y su n a la vista.
Hacerlos condición atrasa la apertura 12–15 semanas (o unas 4–5 si se aprueba
5.1, estimación sin medir) sin cambiar la calidad del dato publicado, que ya
está acotada por las cotas de error. Se siguen informando como avance.

**Entrada a la Fase 1** = apagar la puerta del sitio
(`SAIMC_SITE_GATE_ENABLED=false`), que el plan ya ata al fin del bloque
(`plan-bloque-mvp.md`, decisión 1). Desde ese día el contador de visitantes
únicos sirve de línea de base para los 100 DAU, con sus límites (robots, IP
compartida).

**Alternativa descartada en esta propuesta**: exigir los 500 `approved` antes
de abrir. Sería la lectura literal de §9, pero mezcla un objetivo de volumen
con el criterio de salida, que la especificación separa.

## 6. Entrada sugerida para `docs/decisions.md`

No se anexa en este slice: la decisión es del operador. Texto para pegar si
acepta 5.2 y 5.3 tal cual:

```
## YYYY-MM-DD — Criterio de cierre de la Fase 0
Contexto: H0 da 38,8 % (n = 220, conjunto congelado, B3-2) y no puede ser oficial; hay 226 filas `approved` de 500; las tres cotas de error auditado de la métrica B3 están bajo su meta; 266 filas en USD llevan tasa de relleno (`docs/analisis-fase.md`).
Decisión: la Fase 0 se cierra con H0 registrada sobre el conjunto congelado, la métrica B3 dentro de metas, T1 aplicado y `revalidate --execute` corrido. Los 500 validados se informan como avance, no como condición. La Fase 1 empieza al apagar la puerta del sitio.
Descartado: exigir los 500 `approved` antes de abrir (mezcla un objetivo de volumen con el criterio de salida de §9); etiquetar 80 anuncios a ciegas para una corrida `is_official`.
Consecuencia: la corrida de H0 del día de la transición queda registrada en `docs/h0-runs.jsonl`; el contador de visitantes únicos pasa a ser la línea de base de los 100 DAU.
```
