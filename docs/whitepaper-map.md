# Mapa del whitepaper

Índice de consulta de `whitepaper.md`. Existe para no releer 2100 líneas cada vez
que hay que contrastar una propuesta contra el documento rector.

**Cómo se usa:** buscá la fila que corresponde a lo que estás por proponer, leé
**solo** ese rango de líneas, y recién entonces proponé. Si el documento ya decidió
la cuestión, no hay nada que proponer: hay que implementar lo decidido.

No es una camisa de fuerza. Apartarse del whitepaper es legítimo, pero hay que
decirlo explícitamente y dejar el motivo en `decisions.md`. Lo que no es legítimo es
apartarse sin saber que se estaba apartando.

---

## Vigencia del mapa

- Mapea: **whitepaper v3.5**, 2100 líneas.
- Antes de confiar en los números de línea: `wc -l docs/whitepaper.md`. Si no da
  2100, los números están corridos y hay que regenerar el mapa.
- Anclas de respaldo, siempre vigentes: `rg -n '^#{1,4} ' docs/whitepaper.md`.
  Los números de sección (§6.1, §10.6) sobreviven a las ediciones; los de línea no.

---

## Antes de proponer → dónde verificar

| Si vas a proponer algo sobre… | Sección | Líneas | Qué tan atado está |
| --- | --- | --- | --- |
| Captura, bandeja de entrada, cola de curaduría | §6.1 Etapa 1 | 550–581 | **Vinculante** |
| Inmutabilidad del archivo original | §6.1 | **568** | **Vinculante** |
| OCR, extracción asistida, quién decide | §6.1 | **583–597** | **Vinculante** |
| Esquema de `raw_ingestions` | §7.1 | 1282–1307 | **DDL completo** |
| `products`, `prices_history` | §7.2 | 1309–1362 | Campos + conversión monetaria |
| Conversión de moneda, tasa de cambio | §6.3 E | **872–901** | **Vinculante** |
| Intelligence Layer, agregados | §7.3 | 1364–1406 | Prosa — diseño abierto |
| Reglas de validación | §6.2 | 695–789 | Prosa detallada |
| Score, estados de validación | §6.2 | 738–808 | Prosa |
| H0, umbrales de decisión | §3 | 282–302 | **Apartado 2026-08-12** — la tabla de veredictos (290–296) ya no rige; el párrafo 300 sí |
| Métricas de extracción | §12.5 | 1907–1924 | **Vinculante, salvo 1909** — H0 dejó de ser la única métrica de Fase 0 (2026-08-12) |
| Umbrales nuevos sin valor asignado | §12.6 | **1947–1965** | **Vinculante: no inventar** |
| Alcance de Fase 0, entregables | §9 Fase 0 | 1624–1650 | **Vinculante** |
| Criterio de salida de Fase 0 | §9 | **1648** | **Vinculante en su 1ª oración**; el corte por debajo de 70% se apartó el 2026-08-12 |
| Qué provincia se publica y cuándo | §9.0 | **1522–1622** | **Vinculante** |
| Manipulación, ataque coordinado, C1–C4 | §10.7 | **1760–1871** | **Vinculante** |
| Qué se publica de la metodología | §6.4.1 | **944–1008** | **Vinculante** |
| Stack tecnológico | §13.1 | 1969–1991 | **Cerrada** |
| Datos personales, privacidad | §10.6 | 1743–1758 | **Vinculante** |
| Transparency Layer, qué se publica | §6.4 | 903–942 | Prosa |
| Linaje y trazabilidad del dato | §6.5 | 1010–1100 | Prosa |
| Dashboard, visualización | §6.7 | 1175–1256 | Prosa |
| Posicionamiento frente a elTOQUE | §5 | 336–466 | **Vinculante: no revertir** |
| Niveles de producto, monetización | §8.1 | 1420–1492 | Prosa |
| Roadmap, fases posteriores | §9 | 1514–1696 | Prosa |

---

## Especificidad del modelo de datos

Distinguir esto evita los dos errores simétricos: proponer lo ya decidido, y
tratar como decidido lo que nunca se especificó.

| Tabla | Qué hay en el whitepaper | Estado |
| --- | --- | --- |
| `raw_ingestions` | `CREATE TABLE` con tipos, comentarios e índice único | Implementar tal cual |
| `products` | Lista de campos sin tipos ni restricciones | Diseño abierto |
| `prices_history` | Campos, incluidos los cuatro de conversión monetaria (§6.3 E) | Campos atados, tipos abiertos |
| `provinces` | Nombrada en el alcance de Fase 0 (1639) y restringida por §9.0 | Sin definir, pero con una restricción vinculante |

`raw_ingestions` es la **única** tabla con DDL real en todo el documento.

**Restricción vinculante sobre `provinces` (§9.0, línea 1603):** «la incorporación de
una provincia debe ser configuración, no desarrollo». Cualquier diseño que exija tocar
código para sumar una provincia contradice el documento.

**Estado de publicación por provincia.** §9.0 exige que la interfaz distinga entre
provincia publicada e provincia en ingesta sin publicación. Eso es estado que vive en
algún lado: tenerlo en cuenta al diseñar `provinces`.

---

## Contradicciones conocidas

Registradas para no volver a descubrirlas y para no proponer algo que camine
directo hacia ellas.

**1. Qué muestra la Transparency Layer (abierta).**
La línea 568 dice que se publica «literalmente el archivo capturado». La §10.6
mitigación 2 dice que se muestra el anuncio «no la identidad del anunciante», y la
mitigación 3 dice que no se almacenan teléfonos ni datos de contacto. Con texto
pegado las tres se sostienen. Con una captura de WhatsApp o Telegram no: los
píxeles traen nombre del remitente y a menudo teléfono.

Alcance: es un problema de **publicación**, no de almacenamiento. Guardar local en
Fase 0 —un operador, sin auth, sin exponer— es defendible. Publicar la captura tal
cual rompe la mitigación 2.

No bloquea `raw_ingestions`: la salida derivada y redactada es aditiva (tabla nueva
o columna nullable) y no toca la fila inmutable.

**2. Umbral de densidad vs. lanzamiento del dashboard (resuelta en v3.5).**
§9.0 exigía no publicar una provincia sin densidad mínima; §3 dice que H0 no bloquea
el lanzamiento del dashboard. Aplicadas juntas y en sentido literal, el dashboard de
Fase 1 quedaba vacío. Resuelto en §9.0 «Cómo se concilia con las Fases 0 y 1»: la
condición estadística rige desde el día 1 (tamaño de muestra visible, sin titular de
precio); la condición adversarial rige desde Fase 2, cuando se abre la ingesta
comunitaria. Registrado en `decisions.md`.

---

## Apartamientos deliberados

Lo implementado que no coincide con el documento, con su entrada en `decisions.md`.

| Tema | Whitepaper | Implementado | Entrada |
| --- | --- | --- | --- |
| Restricciones de `raw_ingestions` | DDL sin `NOT NULL` | `NOT NULL` en 8 columnas | 2026-08-06 |
| Marcas de tiempo | `TIMESTAMP` | `TIMESTAMPTZ` | 2026-08-06 |

---

## Lo que el whitepaper no define

- Tipos y restricciones de `products`, `prices_history`, `provinces`.
- Qué motor de OCR usar (define el rol del OCR, no la herramienta).
- Cómo se recorta o redacta una captura para publicación.
- Estructura de directorios del proyecto, layout de paquetes Python.
- Estrategia de migraciones más allá de «Alembic desde el primer modelo».
- El valor de ningún umbral introducido en la v3.5. Ver §12.6: **no inventarlos.**
- Qué fuente alternativa de tipo de cambio se usa como respaldo de la TRMI.
