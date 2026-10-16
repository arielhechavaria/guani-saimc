# Reporte de estado del sistema — 2026-10-16

Insumo del plan `docs/plan-bloque-mvp.md`. Solo lectura: no cambió ningún dato.
Cada cifra trae su fuente. Lo que no se pudo comprobar está marcado
**«no verificado»**.

Límite de este reporte: los conteos salen de la base local del operador
(`prices_history`, 1101 filas). No se consultó producción, y desde el worktree
no hubo acceso a la base (sin `.env`), así que las cifras de la tasa de cambio
(sección 5) siguen sin comprobar.

## 1. Fase en la que está el sistema

**Sigue en Fase 0 por la letra de la especificación; por alcance ya la
superó.** La decisión de darla por cerrada es del operador.

| Criterio (`spec.md` §9) | Estado | Fuente |
|---|---|---|
| 500 registros de 3–5 productos en 2 provincias | Cumplido en bruto: 1101 filas, 5 productos, 2 provincias | `prices_history` |
| «H0 respondida con un número» (criterio de salida) | Medido, pero la última corrida **no es oficial** | `docs/h0-runs.jsonl`, 2026-10-10, `is_official: false` |
| Conjunto de referencia de 300+ | 436 miembros | `reference_set_members` |

- **No hay estado «validado» limpio.** 808 filas están `unverified`; además
  hay 242 `possible_duplicate`, 28 `inconsistent`, 13 `anomaly` y 10
  `incomplete`. «500 validados» no se puede afirmar sin definir qué estado
  cuenta como validado.
- **Procedencia:** human 461, auto 338, audited 302.
- **Provincias:** La Habana 830, Santiago de Cuba 271.
- **Productos:** Arroz 361, Azúcar blanca 226, Pollo 208, Aceite 162,
  Picadillo 134, más filas basura (por ejemplo «Precio :»). Parecen alias mal
  normalizados (no verificado).
- **H0, corrida del 2026-10-10** (220 anuncios, 353 filas): producto 0,853,
  provincia 0,924, precio 0,544, cantidad/unidad 0,550, **extracción completa
  0,388**, tasa de descarte 0,834.
- **El criterio formal ya se movió dos veces.** El 2026-08-12 H0 dejó de
  decidir si el proyecto avanza (`spec-map.md:61`). El 2026-10-10 (B3) la
  métrica de Fase 0 pasó a ser la automatización con su error auditado
  (`spec-map.md:156`).

**Qué falta para declarar la transición** (decisión del operador, a registrar
en `docs/decisions.md`): (a) qué estado de fila cuenta como «validado»;
(b) si la corrida oficial de H0 se hace ahora o se acepta la del 10/10;
(c) si el criterio vigente es el de automatización auditada.

## 2. Inventario

- `src/saimc`: `exchange_rate/`, `extraction/`, `ingestion/`, `instagram/`,
  `llm/`, `marketing/`, `models/` (20 modelos), `validation/`, `web/`, más
  módulos sueltos (`alerting`, `automation`, `cells`, `config`, `db`,
  `privacy`, `units`).
- 32 migraciones Alembic (versión 0032). 96 archivos de test con 3650
  `def test_`. (Cuenta de funciones; la suite no se ejecutó para este
  reporte.)
- `raw_ingestions` 6504, `jev_signals` 4257.
- Timers en `systemd/`: prefill (cada 3 h), alerts (cada 15 min), backup
  (03:00), instagram (cada 3 h, minuto :30). Instalados en este host:
  alerts, backup, prefill y `saimc.service`; **`saimc-instagram` no aparece
  instalado** (no verificado si lo está en producción).
- WAHA corre en Docker (`deploy/waha/docker-compose.yml`), la excepción de la
  decisión cerrada 4.

## 3. Ramas sin integrar

`master` (9625811) coincide con `origin/master`. Seis ramas con trabajo sin
mergear:

| Rama | Commits | Remoto |
|---|---|---|
| `worktree-capa-mayorista` | 6 | no |
| `worktree-prefill-reproponer` | 2 | no |
| `worktree-prompt-caching` | 2 | no |
| `worktree-prefill-status-aislado` | 1 | no |
| `worktree-analisis-carril1` | 1 | no |
| `feat-ariel-ajustes-ui` | 1 | sí |

Dos hallazgos del working tree de `master`:

- `D .venv`: el symlink que se commiteó por error en el incidente del 13/10.
  El índice lo sigue teniendo como symlink (modo 120000). Hay que decidir si
  se commitea su baja o se restaura; no es un slice.
- Este plan nace de `master` sin la capa mayorista (sigue en su rama).

## 4. Jev: qué hace, si se usa, si corre solo

- **Qué es:** un clasificador de TypeSafe AI vía Vercel AI Gateway. Contesta
  tres preguntas cerradas por anuncio: triage sí/no, producto y moneda
  (`validation/jev_client.py:1-25`).
- **Se dispara solo:** sí. Lo llama `extraction/prefill.py` desde
  `saimc-prefill.timer` (cada 3 h). El `.env` local tiene
  `SAIMC_LLM_JEV_ENABLED=true`. La última fila de `jev_signals` es del
  2026-10-16 12:01.
- **Se usa para decidir:** **no.** Está en modo sombra: no entra a ninguna
  regla (`spec-map.md:150`; `lane1.py:100`, «Jev no separa»). Su salida se
  consume en el informe de sombra (`validation/shadow.py`) y se muestra en
  `/control`.
- **Calidad medida (A6):** triage 95,6 %, producto 96,8 %, moneda 94,3 %.
- **Lectura:** corre y cuesta (tope `SAIMC_LLM_JEV_MAX_COST`), pero hoy no
  cambia ningún resultado. Es una decisión pendiente: promoverlo a señal o
  apagarlo.

## 5. Tasa de cambio (tarea T1)

- `SAIMC_EL_TOQUE_API` se lee en `config.py:43-47`. **Sin la variable, el
  cliente devuelve `None` sin hacer HTTP** (`exchange_rate/eltoque.py:255`).
- La cadena de degradación (`exchange_rate/service.py:133-161`): caché exacta
  de la fecha → consulta fresca → **`cache.latest()`** (la última tasa
  guardada, sea de la fecha que sea) → sin tasa (`price_cup` NULL).
- Las filas del paso 3 quedan con `exchange_rate_source = 'elTOQUE (cache)'`.
  Es la marca para encontrarlas. Las **264 filas** del operador son
  **no verificado**: hace falta esta consulta contra producción:
  `SELECT currency, exchange_rate_source, count(*), min(exchange_rate_date),
  max(exchange_rate_date) FROM prices_history GROUP BY 1,2;`
- **El backfill actual no las recalcula**: solo toma filas con
  `exchange_rate IS NULL` (`exchange_rate/backfill.py:68-71`), y estas ya
  tienen tasa.
- elTOQUE sí permite consultar por fecha, un día por llamada; un día sin dato
  responde 200 con `tasas: {}` (`eltoque.py:171-177`).
- **Riesgo adicional:** la caché `exchange_rate_cache` puede contener filas de
  relleno del mismo paso 3. Hay que revisarla antes de confiar en ella.
- `.env` apunta a producción y no hay base de desarrollo (`backfill.py:5-11`):
  cualquier corrida de saneamiento se hace sobre datos reales (ver T2).

## 6. Interfaz pública (tareas T5, T6, T7)

- **Explorar:** el desglose por formato etiqueta el grupo («paquete 1 kg») pero
  las cifras son precio por unidad canónica (lb). `explore_view.py:358-380`;
  la conversión ocurre antes, en `cells.py:1038-1090`. Existe
  `units.convert_quantity` (`units.py:160`) que no usa esta vista.
- **Filtros:** no hay un estado de filtros único. `product_nav._query`
  (`product_nav.py:243-299`) arma URLs, pero `build_window_nav`,
  `build_province_nav`, `build_layer_nav`, `period_hidden`, `map_href` y
  `_product_link` construyen las suyas. Pérdidas confirmadas por lectura:
  `_product_link` pierde `vista` y `pestana`; el formulario «Fechas» pierde
  `pestana`, `capa` y `ver`; el enlace del mapa en la vista liviana pierde la
  vista de origen.
- **Bug de provincia en el tablero (no reproducido):** el candidato más
  probable es que `build_watchlist` lista todos los productos piloto aunque no
  tengan celda en la provincia enfocada, y ordena por el total de todas las
  provincias (`watchlist_view.py:215-322`).
- **Rangos:** `WINDOW_CHOICES = (7, 30, 90)` (`product_nav.py:30`), con
  `DEFAULT_WINDOW_DAYS = 30` que además alimenta descargas, `/control` y la
  landing. Pasar a 3/7/90 obliga a elegir un nuevo valor por defecto.

## 7. Auditorías (tarea T9)

- **Carril 0:** mide falsos descartes (cota del 95 %, metas 10 % y 20 %);
  pantalla `/curation/auditoria`.
- **Carril 1:** mide error por anuncio (cambio que afecta el precio
  publicado). Se audita todo hasta 60; luego una muestra semanal de hasta 30
  (`lane1.py:126`).
- **Última cifra documentada** (`decisions.md:12621`): 2 errores en 60
  auditados = 3,3 % puntual, **10,1 % de cota superior**, que frenó el carril.
  El operador reinició la medición. Después del reinicio **no hay cifra**.
- **Inconsistencia:** `SAIMC_LANE1_AUDIT_SINCE` vale 2026-10-15 en `.env` y la
  decisión dice 10-14.
- **Pendientes registrados:** 2 casos «No era un anuncio válido» sin causa
  investigada (`decisions.md:12624`); si no se audita, el freno nunca salta y
  Telegram no avisa que falta auditar (`decisions.md:12600`).
- La próxima muestra semanal es el **lunes 2026-10-19**.

## 8. Carril 1 (tarea T11)

Foto del 2026-10-16: entran 35 de 257 textos pendientes con propuesta. Los
frenos están en el mensaje original del operador; las causas en el código:

- Tope de 4 filas por anuncio: `lane1.py:107` (`lane1_max_rows=4`; subió de 1
  a 4 el 2026-10-13).
- «El modelo descarta pero las reglas ven un producto»: `engine.py:464`;
  condición: el texto tiene al menos un dígito y menciona un producto piloto.
  Las reglas solo detectan el producto, **no proponen filas**.
- «Otra base»: `rules.py:65` (`check_quantity_price`): compara la lectura
  por unidad contra la de lote, contra la mediana de la celda
  (`cells.evaluate_basis`, mínimo 5 referencias).
- «Canal sucio»: `engine.py:101`. Un canal **sin historial** ya no frena
  (`_NON_BLOCKING_REASONS`, `engine.py:120`); un canal **sucio** sí, por ser
  evidencia (decisión G1b).

## 9. Marketing, SEO y analítica (tareas T8, T10, T16)

- **Tarjetas:** hay 9 tipos (`web/routers/marketing.py`, `CARD_TYPES`),
  renderizadas a PNG con Playwright (`marketing/render.py:30`); Chromium ya
  está en el VPS. El PNG solo se devuelve como descarga; **no se guarda en
  disco**. Las tarjetas duplican a mano los colores de provincia
  (`marketing/cards.py:20`).
- **Instagram:** solo lectura de métricas (Instagram Login, sin página de
  Facebook). El token se pidió con permisos de lectura; **falta**
  `instagram_business_content_publish` para publicar.
- **Decisión que choca:** `decisions.md` (2026-09-28) descartó publicar directo
  a Instagram/Facebook por la revisión de la app de Meta. Publicar requiere una
  entrada nueva que la reemplace.
- **VPS:** 2 vCPU, 3,7 GB de RAM (1,27 GB disponibles), disco al 85 % (5,4 GB
  libres) (`docs/costos.md` y medición del 2026-10-16).
- **SEO que existe:** `<title>`, `lang="es"`, meta description, Open Graph
  básico, un `<h1>` por página, gzip, URL `/producto/{slug}`.
- **SEO que falta:** `robots.txt`, `sitemap.xml`, `rel="canonical"`, og:image y
  Twitter Cards, JSON-LD, favicon y `apple-touch-icon`, política de caché de
  `/static`. `og:url` usa la URL con query, que genera variantes duplicadas.
  Si `SAIMC_SITE_GATE_ENABLED` está activo en producción, no hay SEO posible
  (**no verificado el valor en producción**).
- **Analítica actual:** `analytics.py` emite `public.pageview` con hash de
  IP+día, sin JS de tracking ni cookies.
- **Tour:** la home pública pesa 170 KB con JS en línea; el `localStorage` ya
  se usa para el tema, y la política de privacidad lo menciona.

## 10. Entorno de desarrollo (tarea T2)

No hay Dockerfile, Makefile ni docker-compose de la app. El README documenta
venv con `pip install -e ".[dev]"` y PostgreSQL nativo con `apt`. En la sesión
de este reporte el Postgres local rechazó el usuario por defecto. La decisión
cerrada 4 habla de producción; un compose solo de desarrollo no la rompe en
sentido estricto, pero conviene registrarlo en `decisions.md`.

## 11. Riesgos a tener presentes

1. **No existe base de desarrollo**: el `.env` apunta a producción. Es el
   riesgo operativo más alto del plan (T1 modifica filas reales).
2. 264 filas con tasa de relleno, **no verificado**; y la caché de tasas
   puede estar contaminada.
3. Auditoría reiniciada sin cifra nueva; el freno depende de que alguien
   audite.
4. Publicar en Instagram contradice una decisión vigente.
5. 6 ramas sin push: si se pierde la máquina, se pierde el trabajo.
6. Disco del VPS al 85 %.
