# Instalación de las plantillas Jinja

Las plantillas de `templates/` de este bundle son **código listo para el repo**, no maqueta.
Reemplazan a las que ya existen en `src/saimc/web/templates/`, conservando **todos** los
`name=`, `id=`, `action=`, `method=` y `value=` de los originales — así que `submit_curation`,
`_channel_province_id`, los campos indexados (`product_id_0`, `price_0`, `row_count`…) y los
tests siguen viendo lo mismo.

## Pasos

```bash
# 1. La hoja de estilo
cp saimc-material.css src/saimc/web/static/

# 2. Las fuentes (único paso que requiere descargar algo)
mkdir -p src/saimc/web/static/fonts
#   Roboto y Roboto Mono: los .woff2 de fonts.google.com
#   Material Symbols Rounded:
curl -sSLo src/saimc/web/static/fonts/material-symbols-rounded.woff2 \
  "https://fonts.gstatic.com/s/materialsymbolsrounded/v1/material-symbols-rounded.woff2"
#   (o subseteá con fonttools a las ~30 ligaduras que lista el README)

# 3. Las plantillas
cp templates/*.html src/saimc/web/templates/

# 4. Verificar
.venv/bin/pytest
```

**Hasta que el .woff2 de Material Symbols esté en su lugar, los iconos se ven como palabras**
(`inventory_2`, `add`…) porque son ligaduras. No es un bug de las plantillas.

Cuando todo pase: borrar `static/bootstrap.min.css` (232 KB) y `static/curation.css`. Sus
tokens ya viven en `saimc-material.css` (~22 KB).

## Qué cambia respecto de los originales

| Archivo | Cambio |
| --- | --- |
| `_shell.html` | **Nuevo.** Rail + cabecera comunes. El destino activo se deriva de `request.url.path` — **cero cambios en los routers**. Bloques: `title`, `heading`, `subtitle`, `stats`, `tabs`, `content`, `snackbar`, `scripts`. |
| `catalog.html` | Extiende el shell. Alta de canal y de producto colapsadas en `<details>`. Tabla de canales de 7 a 5 columnas (plataforma y notas bajan a la línea secundaria del nombre). Apagar/Encender pasa a un toggle de icono, mismo POST. Filtro «Solo el foco» vía `?focus=1`, leído con `request.query_params` — sin tocar el router. |
| `curation.html` | Dos columnas: evidencia sticky a la izquierda, formulario a la derecha. Contadores suben a la cabecera. `<details>` de pegado y de «Datos del anuncio» conservados. Barra de acciones sticky al pie. **`removeProductCard` ahora busca `.m3-row-card`** en vez de `.card`. |
| `_price_row_card.html` | Mismos campos, mismos nombres, mismo `edit_mode`. Envase y compra mínima pasan a un `<details>` colapsado. Formato de venta va a ancho completo. |
| `control.html` | Extiende el shell y pierde el `<style>` embebido (todo está en la hoja). Los ~10 párrafos de spec se condensan en dos grillas de 4 notas. Métrica de registros como tarjeta destacada en primary. Columna **Total** nueva en la tabla de cobertura. Ranuras de provincia `.m3-slot-N` en vez de `.saimc-slot-N`. |
| `curation_edit.html` | Extiende el shell, dos columnas como la cola, barra de acciones sticky. |
| `curation_history.html` | Extiende el shell, tabla Material, «Eliminar» pasa a icon-button con el mismo `confirm()`. |

## Lo único que podría requerir tocar tests

- Selectores de clase de Bootstrap (`.card`, `.badge`, `.table`, `.alert`, `.btn-primary`).
  `grep -rn "class" tests/` antes de correr; si un test afirma sobre una de esas clases,
  agregala al lado de la `m3-*` en vez de reescribir el test.
- El texto de los botones «Apagar» / «Encender» de canales ya no existe como texto: ahora es
  un icon-button con `title="Apagar canal"` / `"Encender canal"`. Si un test busca ese texto,
  la opción menos invasiva es volver a poner el `<button>` con texto en esa celda.
- Igual con «🗑 Eliminar» en la tarjeta de fila: ahora es un icon-button con `title`.

Nada más cambia de comportamiento: mismas rutas, mismos POST, mismo patrón sin-JavaScript
(`<details>` nativo, `onchange="this.form.submit()"`, pestañas como enlaces con `?province=`).
