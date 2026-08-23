# Rediseño home pública SAIMC — handoff a Claude Code

## Qué es esto
Rediseño de `/` (home pública, `templates/public_home.html`) explorado en 3
direcciones sobre datos reales del proyecto (mapa de provincias, tarjetas de
precio, series de anuncios). Tratamiento final: **Material 3** (superficies
redondeadas, elevación por sombra, chips en píldora) — igual que el resto de
la app (`saimc-material.css`), en vez del wireframe de esquinas cuadradas del
primer intento.

## Archivos de este paquete
- `Home Pública SAIMC.dc.html` — las 3 opciones de layout, completas, lado a
  lado (sección `id="1a"`, `"1b"`, `"1c"`). Es HTML/CSS inline renderizable
  directo en el navegador; no es Jinja todavía.
- `MapaCuba.dc.html` — el mapa de cobertura como pieza aparte, 3 variantes de
  color (`dark` / `paper` / `wire`) y hover con tooltip por provincia.
- `saimc-data.js` — datos de muestra con el mismo shape que ya devuelven
  `build_map` / `build_cards` / `build_series` del backend real.
- `CLAUDE_PROMPT.md` — el prompt para pegarle a Claude Code (abajo, también
  copiado en este mismo README para no perderlo).

## Decisiones de diseño a preservar en el port
- **Paleta** (reemplaza al teal único `#00696e`):
  - `#05668d` Baltic Blue — estructura, cifras de precio, acento primario.
  - `#028090` Teal — relleno del mapa / codificación del dato.
  - `#C1F2E8` Icy Aqua — acento luminoso sobre fondo oscuro.
  - `#9AAAA6` Ash Grey — sin datos, líneas finas.
  - `#AEC092` Muted Olive — línea de mediana en las series (antes se
    confundía con la banda mín–máx).
- **Tipografía**: Alice (serif) en titulares y cifras grandes; Roboto/Barlow
  existente para el resto. Alice se cargó por Google Fonts para prototipar —
  **hay que auto-hospedarla** (ver Restricciones).
- **Material 3**: tarjetas y paneles con `border-radius` 16–28px y sombra de
  dos capas (`0 1px 2px rgba(5,32,48,.08), 0 4px 14px rgba(5,32,48,.06)`) en
  vez de borde de 1px; estados/etiquetas como chips en píldora
  (`border-radius:999px`) con `background:var(--color-accent-100)` /
  `color:var(--color-accent-700)`.
- **Mapa**: la geometría SVG de `map_cuba.py` no cambia — es dominio público
  (Natural Earth 10m) y ya está en el repo. Solo se reestiliza `fill`/`stroke`
  según observaciones por provincia. El color intensidad es la única
  codificación (nunca doble-codificar con tamaño o texto encima).
- **Series sin línea que une puntos**: es deliberado (ver comentario en
  `public_home.html` original) — no "arreglarlo" agregando una polilínea.

## Restricciones del proyecto (no negociables)
- **Sin CDN en runtime** (`docs/decisions/001-stack-python-unico.md`): la
  fuente Alice tiene que quedar en `static/fonts/` como `.woff2`
  auto-hospedado (~40 KB), no un `<link>` a `fonts.googleapis.com`.
- Sin librerías de gráficos/mapas nuevas — se evaluó ECharts y MapLibre GL y
  no se justifican dado el peso (~170 KB–800 KB) contra la restricción de
  conexión lenta. El SVG inline generado por Jinja sigue siendo la decisión
  correcta.
- La ausencia de datos se comunica como decisión metodológica (§9.0 del
  spec), no como estado vacío o error — mantener ese tono en cualquier copy
  nuevo.

## Cómo verificar el resultado
1. `map.covered`, `map.provinces`, `cards`, `series`, `window_days` deben
   seguir siendo los mismos objetos que ya arma `public.py` — no agregar
   campos nuevos al contexto de la plantilla.
2. Contraste: texto secundario sobre el panel oscuro (`#052030`) en
   `rgba(242,242,243, X)` — usar `X ≥ .58` para paragraph, `.64+` para labels
   con cifras encima (ya validado en el prototipo, mantenerlo al portar).
3. Responsive: las plantillas actuales no tienen breakpoints. Si se porta el
   grid de 3 columnas de "Precios observados", agregar `@media` a 1 columna
   bajo ~640px.

---

## Prompt para Claude Code

```
Portá el rediseño de la home pública de SAIMC (adjunto: Home Pública SAIMC.dc.html,
MapaCuba.dc.html, saimc-data.js, README.md) a las plantillas reales del repo
guani-saimc. Usá la opción [COMPLETAR: 1a / 1b / 1c] del archivo adjunto como
referencia de layout — es HTML/CSS inline, no Jinja; portalo respetando el
{% for %}/{{ }} que ya existe sobre map/cards/series/window_days en
src/saimc/web/templates/public_home.html, sin inventar campos nuevos.

Cambios a aplicar:
1. Reemplazar el <style> del bloque `head` y el markup del bloque `content`
   de public_home.html por el layout de la opción elegida.
2. Nueva paleta como custom properties en saimc-material.css (o en el <style>
   de public_home.html si se sigue esa convención): #05668d (acento primario),
   #028090 (dato/mapa), #C1F2E8 (acento sobre oscuro), #9AAAA6 (sin datos),
   #AEC092 (mediana en series). Reemplaza al teal único #00696e.
3. Auto-hospedar la fuente Alice (usada en titulares/cifras) como .woff2 en
   static/fonts/ — el proyecto prohíbe CDN en runtime. No dejar ningún <link>
   a fonts.googleapis.com.
4. Tratamiento visual Material 3: tarjetas/paneles con border-radius 16-28px
   y sombra de dos capas en vez de borde de 1px; estados como chips en
   píldora (border-radius:999px, fondo accent-100, texto accent-700).
5. El SVG de map_cuba.py no cambia de geometría, solo se reestilizan fill/
   stroke de .m3-map-province según observaciones por provincia.
6. No agregar librerías (ECharts/MapLibre evaluadas y descartadas por peso).
7. Mantener el tono: ausencia de dato = decisión metodológica, no error.

Verificá que public.py sigue recibiendo lo que espera, el contraste de texto
secundario sobre el panel oscuro nuevo, y agregá un breakpoint a 1 columna
bajo ~640px si portás el grid de 3 columnas.
```
