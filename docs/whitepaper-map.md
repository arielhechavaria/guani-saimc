# Mapa del whitepaper

Índice de consulta de `whitepaper.md`. Existe para no releer 1615 líneas cada vez
que hay que contrastar una propuesta contra el documento rector.

**Cómo se usa:** buscá la fila que corresponde a lo que estás por proponer, leé
**solo** ese rango de líneas, y recién entonces proponé. Si el documento ya decidió
la cuestión, no hay nada que proponer: hay que implementar lo decidido.

No es una camisa de fuerza. Apartarse del whitepaper es legítimo, pero hay que
decirlo explícitamente y dejar el motivo en `decisions.md`. Lo que no es legítimo es
apartarse sin saber que se estaba apartando.

---

## Vigencia del mapa

- Mapea: **whitepaper v3.4**, 1615 líneas.
- Antes de confiar en los números de línea: `wc -l docs/whitepaper.md`. Si no da
  1615, los números están corridos y hay que regenerar el mapa.
- Anclas de respaldo, siempre vigentes: `rg -n '^#{1,3} ' docs/whitepaper.md`.
  Los números de sección (§6.1, §10.6) sobreviven a las ediciones; los de línea no.

---

## Antes de proponer → dónde verificar

| Si vas a proponer algo sobre… | Sección | Líneas | Qué tan atado está |
| --- | --- | --- | --- |
| Captura, bandeja de entrada, cola de curaduría | §6.1 Etapa 1 | 419–448 | **Vinculante** |
| Inmutabilidad del archivo original | §6.1 | **437** | **Vinculante** |
| OCR, extracción asistida, quién decide | §6.1 | **452–456** | **Vinculante** |
| Esquema de `raw_ingestions` | §7.1 | 1058–1077 | **DDL completo** |
| `products`, `prices_history` | §7.2 | 1085–1124 | Solo campos — diseño abierto |
| Intelligence Layer, agregados | §7.3 | 1128–1170 | Prosa — diseño abierto |
| Reglas de validación | §6.2 | 564–658 | Prosa detallada |
| Score, estados de validación | §6.2 | 619–677 | Prosa |
| H0, umbrales de decisión | §3 | 247–268 | **Vinculante** |
| Métricas de extracción | §12.5 | 1442–1460 | **Vinculante** |
| Alcance de Fase 0, entregables | §9 Fase 0 | 1286–1310 | **Vinculante** |
| Criterio de salida de Fase 0 | §9 | **1310** | **Vinculante** |
| Stack tecnológico | §13.1 | 1484–1507 | **Cerrada** |
| Datos personales, privacidad | §10.6 | 1391–1404 | **Vinculante** |
| Transparency Layer, qué se publica | §6.4 | 741–778 | Prosa |
| Linaje y trazabilidad del dato | §6.5 | 782–872 | Prosa |
| Dashboard, visualización | §6.7 | 947–1028 | Prosa |
| Niveles de producto, monetización | §8.1 | 1184–1257 | Prosa |
| Roadmap, fases posteriores | §9 | 1278–1350 | Prosa |

---

## Especificidad del modelo de datos

Distinguir esto evita los dos errores simétricos: proponer lo ya decidido, y
tratar como decidido lo que nunca se especificó.

| Tabla | Qué hay en el whitepaper | Estado |
| --- | --- | --- |
| `raw_ingestions` | `CREATE TABLE` con tipos, comentarios e índice único | Implementar tal cual |
| `products` | Lista de campos sin tipos ni restricciones | Diseño abierto |
| `prices_history` | Lista de campos sin tipos ni restricciones | Diseño abierto |
| `provinces` | Solo nombrada en el alcance de Fase 0 (línea 1301) | Sin definir |

`raw_ingestions` es la **única** tabla con DDL real en todo el documento.

---

## Contradicciones conocidas

Registradas para no volver a descubrirlas y para no proponer algo que camine
directo hacia ellas.

**1. Qué muestra la Transparency Layer (abierta).**
La línea 437 dice que se publica «literalmente el archivo capturado». La §10.6
mitigación 2 dice que se muestra el anuncio «no la identidad del anunciante», y la
mitigación 3 dice que no se almacenan teléfonos ni datos de contacto. Con texto
pegado las tres se sostienen. Con una captura de WhatsApp o Telegram no: los
píxeles traen nombre del remitente y a menudo teléfono.

Alcance: es un problema de **publicación**, no de almacenamiento. Guardar local en
Fase 0 —un operador, sin auth, sin exponer— es defendible. Publicar la captura tal
cual rompe la mitigación 2.

No bloquea `raw_ingestions`: la salida derivada y redactada es aditiva (tabla nueva
o columna nullable) y no toca la fila inmutable.

---

## Lo que el whitepaper no define

- Tipos y restricciones de `products`, `prices_history`, `provinces`.
- Qué motor de OCR usar (define el rol del OCR, no la herramienta).
- Cómo se recorta o redacta una captura para publicación.
- Estructura de directorios del proyecto, layout de paquetes Python.
- Estrategia de migraciones más allá de «Alembic desde el primer modelo».
