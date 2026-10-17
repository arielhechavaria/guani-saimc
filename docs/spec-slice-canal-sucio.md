# Slice canal-sucio-no-frena — el canal sucio deja de frenar si reglas y modelo coinciden

Estado: aprobada por el operador el 2026-10-17 (es su propuesta). Toca el motor
de validación: los casos se escribieron antes que el código.

## Contexto

Hoy la regla 4 **fallada** («el canal tiene un historial sucio») saca al
anuncio del carril 1 (`engine._blocks_lane1`). Lo decidió B2b
(`docs/decisions.md`, 2026-10-09, «G1b se abre adelantada» y «B2b aprobada»):
el canal sucio era lo que separaba los errores de Groupej Morales Surl en el oro
v7. Desde el 2026-10-13 el desacuerdo entre reglas y modelo ya no frena
(`lane1_model_over_rules`): manda el modelo. Con eso, en un canal sucio no queda
ningún segundo lector que confirme lo que leyó el modelo, y el canal frena
siempre.

Propuesta del operador: si el canal sucio es el **único** motivo y reglas y
modelo **coinciden**, el anuncio entra. Las dos lecturas independientes que
dicen lo mismo hacen de verificación; el canal sucio dice que el modelo suele
equivocarse ahí, no que esta lectura esté mal.

## La regla

Con `ValidationConfig.lane1_dirty_channel_if_agree = True`, un anuncio cuyo
**único** freno es la regla 4 fallada en sus filas entra al carril 1 si en
**todas** sus filas reglas y modelo coinciden en producto, precio, moneda y
provincia (la condición 2 del carril 1 de `docs/spec-motor-validacion.md`, la
de antes del 2026-10-13).

- «Único» quiere decir: ningún otro motivo del anuncio (imagen, filas caídas,
  sin filas, tope de filas, sin configuración), ningún otro motivo de fila que
  frene, y ninguna señal externa que discrepe.
- «Coinciden» es lo que ya mide `compare_with_rules`: si una fila trae
  «reglas y modelo no coinciden en …», no coinciden. Una fila sin fila de las
  reglas (`sin_regla`) no coincide. La moneda coincide si los dos dan la misma,
  esté escrita o no (G1b).
- La fila **no cambia**: conserva el motivo y su estado `untrusted_source`.
  Cambia solo el carril, igual que con el canal sin historial (B2b-1).
- Si el anuncio no entra, el motivo del canal sucio sigue listado junto a los
  demás: el informe y el comando cuentan qué lo frena de verdad.
- Apagado por defecto (`False`): el informe de sombra y las demás
  configuraciones siguen midiendo lo de antes. `lane1.CONFIG` lo enciende.

## Casos esperados (escritos antes que el código)

Con la configuración del carril 1 de hoy (`CONFIG_MODELO_MANDA` de los tests:
canal sucio con 3 decisiones y más de la mitad tocadas, tope de 4, manda el
modelo) más el interruptor nuevo. Canal sucio = 3 decisiones, 2 de 3 filas
tocadas.

| # | Anuncio | Carril | Motivos / estado |
|---|---------|--------|------------------|
| S1 | «Arroz 320 CUP la libra, Habana», canal sucio, reglas = modelo | 1 | fila `untrusted_source` con «el canal tiene un historial sucio» |
| S2 | igual, pero el modelo se abstiene en la unidad | 2 | «fila 1: el modelo se abstuvo en unidad» y «fila 1: el canal tiene un historial sucio» |
| S3 | igual que S1, pero 5 filas con tope 4 | 2 | el tope y el canal sucio |
| S4 | canal sucio, las reglas leen 330 y el modelo 320 | 2 | «fila 1: el canal tiene un historial sucio» (el desacuerdo, solo, no frena) |
| S5 | canal sucio, las reglas no tienen ninguna fila | 2 | el canal sucio |
| S6 | dos filas en canal sucio: la 1 coincide, la 2 no | 2 | el canal sucio en las dos filas |
| S7 | S1 con el interruptor apagado | 2 | el canal sucio (lo de hoy) |
| S8 | S1 con una señal externa que discrepa | 2 | la señal y el canal sucio |

## Qué no cambia

- Las seis reglas, el estado de cada fila, el puntaje, `validate_stored_row` y
  `ENGINE_VERSION`: ninguna fila guardada cambia de estado.
- El carril 0, la auditoría, el freno por error auditado y la celda que diverge.
- Un canal sucio con desacuerdo sigue en el carril 2: ahí no hay segundo lector.

## Medición

`python -m saimc.validation.lane1` en seco, antes y después, sobre los textos
pendientes con propuesta: cuántos anuncios pasan del carril 2 al 1.

Resultado (producción, 2026-10-17): antes entraban 0 de 253; después, 1
(d4c0cefd, EL BODEGÓN DOBLE VÍA). El canal sucio aparecía en 13 y era el único
motivo en 9; de esos 13, los otros 12 discrepan siempre en moneda y precio a la
vez (8 de VENTAS D'ALE, 4 de EL BODEGÓN) y siguen en el carril 2.

## Contraste

Reemplaza, de la entrada «G1b se abre adelantada» del 2026-10-09 y de
«Precisiones de B2b» de `docs/spec-motor-validacion.md`, el punto «la regla 4
fallada (canal sucio) sigue frenando», y de la entrada del 2026-10-13 «En el
carril 1 manda el modelo», el canal sucio en la lista de lo que sigue frenando:
ahora frena solo si reglas y modelo no coinciden o hay otro motivo.
