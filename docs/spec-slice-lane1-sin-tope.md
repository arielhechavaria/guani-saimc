# Slice lane1-sin-tope — el tope de filas del carril 1 pasa a ser configurable y se quita

Estado: aprobada por el operador el 2026-10-19 («hay que quitar esa regla»). Toca el
motor de validación: los casos se escriben antes que el código.

## Contexto

`ValidationConfig.lane1_max_rows` (4, fijo en `lane1.CONFIG`) saca del carril 1 a todo
anuncio cuya propuesta trae más filas (`engine.py`, «la propuesta trae más filas que
el tope del carril 1»). La entrada del 2026-10-17 «El tope del carril 1 sigue en 4
filas, ahora medido con la auditoría» lo dejó en 4 y dijo que subirlo exigía la
aprobación del operador y evidencia propia de los anuncios largos.

El operador lo aprueba: las listas largas de los almacenes mayoristas traen cada vez
más productos piloto y con el tope no entrarían al sistema. Hoy frena a 77 de los 241
textos pendientes con propuesta y **solo a ese motivo** a 43 (seco del 2026-10-19).

La medición del 2026-10-17 (198 anuncios auditados) mostró que el error por anuncio
**no crece** de 1 a 4 filas (9/127, 4/40, 0/22, 0/9). De 5 filas o más **no hay
ninguna auditoría**, porque el carril nunca aceptó una. Eso es lo que se asume al
quitar el tope; se mide después, no antes.

## La regla

- El tope sale de `lane1.CONFIG` y pasa a una variable de entorno,
  `SAIMC_LANE1_MAX_ROWS` (entero). **Sin definir vale 0 = sin tope.** Un valor positivo
  N reinstala el tope en N (vuelta atrás sin tocar código); un valor negativo es un
  error de configuración al arrancar.
- Con sin tope, un anuncio de texto con cualquier número de filas entra al carril 1 si
  cumple todo lo demás de hoy. Las condiciones por fila, los motivos y su orden no
  cambian; cuántas filas trae solo deja de ser un motivo.
- `ValidationConfig.lane1_max_rows` sigue existiendo como `int | None` (`None` = sin
  tope). El valor por defecto del dataclass **no cambia** (el informe de sombra y los
  tests que lo usan siguen midiendo lo de antes); lo que cambia es el valor con que
  `lane1.CONFIG` se arma a partir del ajuste.
- `--por-filas` (`python -m saimc.validation.lane1 --por-filas`) debe poder mostrar el
  error auditado de los anuncios de 5 filas o más cuando existan; hoy imprime «tope
  vigente: 4». Debe decir «sin tope» cuando no lo hay y mostrar los estratos de 5 o más
  filas con su n y su cota.

## Casos esperados (escritos antes que el código)

Con la configuración del carril 1 de los tests de hoy y un anuncio de texto limpio
(todas las filas verificadas, reglas y modelo coinciden), salvo que se diga otra cosa.

| # | Anuncio | Tope | Carril | Motivos |
|---|---------|------|--------|---------|
| T1 | 4 filas | 4 | 1 | — (lo de hoy) |
| T2 | 5 filas | 4 | 2 | «la propuesta trae más filas que el tope del carril 1 (4)» (lo de hoy) |
| T3 | 5 filas | sin tope | 1 | — |
| T4 | 12 filas | sin tope | 1 | — |
| T5 | 12 filas, la fila 7 con una regla fallada que frena | sin tope | 2 | solo el motivo de la fila 7; ninguno de tope |
| T6 | 8 filas, el modelo se abstuvo en la unidad de la fila 3 | sin tope | 2 | «fila 3: el modelo se abstuvo en unidad» |
| T7 | imagen con 6 filas | sin tope | 2 | el carril 1 no acepta imágenes (lo de hoy) |
| T8 | 5 filas | 6 | 1 | — |
| T9 | 7 filas | 6 | 2 | el tope (6) |

Ajuste (`Settings`):

| # | `SAIMC_LANE1_MAX_ROWS` | `lane1_max_rows` resultante |
|---|---|---|
| C1 | sin definir | sin tope (`None`) |
| C2 | `0` | sin tope |
| C3 | `4` | 4 |
| C4 | `-1` | error al validar la configuración |

`lane1.CONFIG` (o lo que lo reemplace) refleja el ajuste: con C1 el carril acepta T3.

## Qué no cambia

- Las seis reglas, el estado de las filas, el puntaje, `validate_stored_row` y
  `ENGINE_VERSION`.
- Que el carril 1 solo acepte textos, el freno por error auditado (global), el
  arranque, la auditoría y el canal sucio.
- El valor por defecto del dataclass y, por tanto, el informe de sombra.

## Riesgo asumido

1. **El error por anuncio puede crecer con las filas** (basta una fila mal para que el
   anuncio cuente como error) y de 5 o más no hay muestra. Si cada fila fallara con
   independencia en 5 %, un anuncio de 8 filas fallaría en 34 %. Los datos de 1 a 4
   filas no lo muestran, y en las listas largas de un mismo almacén los errores se
   repiten por plantilla, pero no está medido.
2. **El freno por error es global.** Si los anuncios largos erran, la cota inferior
   puede pasar la meta (10 %) y frenar el carril 1 entero, también para los de 1 a 4
   filas. Es la razón por la que la entrada del 2026-10-17 proponía un freno por
   estrato; este slice no lo implementa (fuera de alcance: cambia el motor más).
3. **Cómo se corrige sin código:** poner `SAIMC_LANE1_MAX_ROWS=4` en el `.env` y
   reiniciar vuelve a lo de antes. Lo ya aceptado con más filas sigue aceptado; se
   puede ver con `--por-filas`.
