# Slice descarte-incoterm — el carril 0 descarta los precios de importación

Estado: **pedido por el operador el 2026-10-26** («que lo descarte solo en el
carril 0»). Toca el motor de validación: flujo completo (`CLAUDE.md`, «Proceso»).

## 1. El problema

La única fila en EUR de la base salió de un anuncio de importación:

> Llega 22/10 · Contenedor de aceite Girasol 1lt · 3630 cajas x 15 unid ·
> Precio CIF : 1.86€ ( se puede pagar en usd ) · 🚨PAGO EN EL EXTERIOR 🚨

El carril 1 lo aceptó solo como «Aceite, 1,86 EUR por 15 l, La Habana»: unos
110 CUP/l, contra una mediana de 2.453 CUP/l del aceite en los últimos 30 días.

Un precio **CIF** (*Cost, Insurance and Freight*) es el de la mercancía en el
puerto de destino, con flete y seguro pagos, **antes de nacionalizar**:
aranceles, despacho y transporte interno corren por cuenta del comprador.
**CFR** es lo mismo sin el seguro. **FOB** y **EXW** son precios en origen.
Ninguno es un precio del mercado interno, que es lo que mide SAIMC.

## 2. Medición (base del VPS, 2026-10-26, solo lectura)

`import_price_term` contra todos los `raw_ingestions`:

| Término | Descartados | Curados |
| --- | --- | --- |
| CIF | 12 | 2 |
| CFR | 13 | 4 |
| «pago en el exterior» | 8 | 0 |
| FOB / EXW | 0 | 0 |

Los 6 curados son todos ofertas de importación por contenedor (azúcar CFR,
arroz de Guyana CIF, aceite de soja CFR Mariel, pollo CFR Mariel ×2, aceite
CIF): **ningún falso positivo** en 39. FOB y EXW no aparecen todavía; entran
porque son la misma clase de precio.

## 3. Especificación

- **S1.** `engine.import_price_term(text)` devuelve `"CIF"`, `"CFR"`, `"FOB"`,
  `"EXW"` (sigla suelta, sin distinguir mayúsculas) o `"pago en el exterior"`
  (también «pagos en el exterior»), o `None`. «cifra» no cuenta; «c.i.f.» con
  puntos tampoco: no se adivina.
- **S2.** `decide_lane_0` manda al carril 0 todo anuncio con un término, antes
  de mirar al modelo: aunque proponga filas, aunque no haya propuesta, aunque
  nombre un piloto. Motivo: «precio de importación (CIF): no es un precio del
  mercado interno».
- **S3.** `validate_ad` hace lo mismo con configuración (el carril 1 nunca lo
  acepta); sin configuración, a la cola, como todo.
- **S4.** `lane0.apply_lane_0` lo escribe con `discard_reason = off_focus` y
  `detail = {"decider": "import_price", "term": <término>}`. Sin filas de precio.
- **S5.** Sigue gobernado por los interruptores de siempre
  (`SAIMC_LANE0_TEXT_ENABLED`, `SAIMC_LANE0_IMAGE_ENABLED`; en una imagen, el
  texto es su OCR) y cuenta en la cota y la auditoría semanal de su tipo.

## 4. Fuera de alcance

- Las 8 filas que ya publicaron esos 6 anuncios: se limpian a mano desde la
  interfaz (auditoría o «Corregir un anuncio curado»).
- `ENGINE_VERSION` no cambia: versiona las reglas de cada fila, y esta regla
  decide el carril del anuncio, no el estado de una fila.
