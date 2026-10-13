# Slice — Seguimiento de la cuenta de Instagram de Guani

Estado: **PROPUESTA, sin aprobar.** Nada programado. Fuera del alcance de la Fase 0
(no toca el pipeline de precios): se abre por pedido explícito del operador
(2026-10-13).

## Qué resuelve

Ver en `/marketing` cómo rinden las publicaciones de la cuenta propia (posts,
reels, stories) sin entrar a Meta Business Suite, y conservar el historial de
métricas, que Meta solo da con ventana limitada y cambia con el tiempo.

## Decisiones previas que cruza

- `docs/decisions.md` (2026-09-28) descartó **publicar** desde el sistema porque
  pide revisión de la app de Meta. Este slice **solo lee** la cuenta propia: no
  contradice ese descarte. Se registra una entrada nueva al implementar.
- Decisión cerrada 2 (nada de scraping): se usa la API oficial con la cuenta
  propia autorizada, no scraping.
- Decisión cerrada 3 (sin cola): un lote con temporizador de systemd que lee,
  escribe y termina, igual que `saimc-prefill`.

## Requisitos de Meta (verificados contra la documentación oficial, 2026-10-13)

- Cuenta profesional (Business o Creator). Con *Instagram API with Instagram
  Login* no hace falta página de Facebook.
- Permisos: `instagram_business_basic` e `instagram_business_manage_insights`.
- App Review: la tabla de Meta marca «no requerido» para una app que solo usa un
  negocio propio (Standard Access). **Se confirma con la primera llamada real.**
- Token de larga duración: se renueva pasadas 24 h de vida y dura 60 días desde
  cada renovación. Si caduca, no se puede renovar: hay que repetir el alta a mano.
- Métricas: `views`, `reach`, `likes`, `comments`, `saved`, `shares`,
  `total_interactions`; reels suman `ig_reels_avg_watch_time`. `impressions` está
  deprecada para contenido posterior al 2024-07-02. Carruseles sin insights.
  Stories: métricas solo durante 24 h. Los datos pueden demorar hasta 48 h.

## Esquema propuesto (migración 0032)

**`instagram_media`** — una fila por publicación.
`id` uuid PK · `ig_media_id` text único · `media_type` (FEED / REELS / STORY, con
CHECK) · `permalink` · `caption` (texto propio de Guani) · `posted_at` ·
`first_seen_at` · `last_synced_at`.

**`instagram_media_metrics`** — una fila por publicación y por lectura (serie
temporal, solo se agrega, no se modifica).
`id` uuid PK · `media_id` FK · `captured_at` · `views` · `reach` · `likes` ·
`comments` · `saved` · `shares` · `total_interactions` · `avg_watch_time_ms`
(null salvo reels). Único `(media_id, captured_at)`.

**`instagram_account_snapshots`** — una fila por lectura de la cuenta.
`id` uuid PK · `captured_at` · `followers_count` · `media_count`.

**`instagram_credentials`** — fila única con el token vigente (ver pregunta
abierta).
`id` · `access_token` · `expires_at` · `refreshed_at`.

Ninguna tabla guarda datos de terceros: ni comentarios, ni usuarios que
interactúan. Solo cifras agregadas de la cuenta propia.

## Piezas

1. `saimc/instagram/client.py` — transporte `httpx` separado del parseo puro
   (patrón de `exchange_rate/eltoque.py`); sin token o ante error, degrada sin
   levantar excepción.
2. `saimc/instagram/sync.py` — CLI (`parse_args`/`main`, en seco por defecto,
   `--execute` para escribir): lista publicaciones, lee métricas, agrega filas y
   renueva el token si faltan menos de 15 días.
3. `systemd/saimc-instagram.{service,timer}` — `oneshot`, cada 3 h (las stories
   viven 24 h; con 3 h se capturan varias veces). Con su test equivalente a
   `tests/test_systemd_units.py`.
4. Sección «Instagram» en `/marketing` (ya protegido por `require_operator`):
   tabla de publicaciones con sus últimas métricas y seguidores en el tiempo.
5. Variables nuevas en `env.example` (`SAIMC_INSTAGRAM_APP_ID`, etc.) y comando
   en `docs/comandos.md`.

## Tests

El caso esperado se define antes del parseo: cuerpos de respuesta de la Graph API
escritos a mano con la forma documentada, y `httpx.MockTransport` sin red.
Renovación de token y degradación sin token, con casos propios. La sección de
`/marketing` responde 401 sin credenciales (ya lo exige `test_route_inventory`).

## Lo que el operador tiene que hacer a mano (no lo puedo hacer yo)

1. Pasar la cuenta de Instagram a Business o Creator, si no lo es.
2. Crear una app en Meta for Developers con el producto *Instagram* y agregar la
   cuenta como tester/admin.
3. Generar el token de larga duración y pasármelo por `.env` (nunca por chat).

## Fuera de alcance

Publicar, responder comentarios o mensajes, leer cuentas ajenas, gráficos
elaborados, alertas. Se puede abrir como slices aparte.

## Pregunta abierta

¿Dónde vive el token? La renovación produce un token nuevo cada vez, y un `.env`
no se reescribe solo.
- **A (recomendada):** tabla `instagram_credentials`; el lote renueva y guarda ahí.
  El `.env` solo trae el token inicial para el alta.
- **B:** archivo `chmod 600` fuera del repo. Sin tabla, pero un archivo más que
  respaldar y proteger.
