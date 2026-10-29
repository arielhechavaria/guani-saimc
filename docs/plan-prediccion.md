# Plan — Módulo predictivo v1 (reglas básicas en modo sombra)

Estado: **plan aprobado el 2026-10-29, sin implementar.** Se empieza en otra
sesión, por el slice 1. Decisión en `docs/decisions.md` (2026-10-29).

## La idea

La frecuencia de anuncios de un producto mide su oferta. Si la frecuencia de
anuncios de arroz cae por debajo de lo normal, hay escasez y el precio sube.
Es el **Índice de Disponibilidad** de `spec.md` §6.3 C, medido con un conteo y
no con un modelo, así que cabe en la Fase 0.

Junto a ella entra la hipótesis hermana del 2026-10-10 (Engram,
`hypothesis/consenso-fuentes-alza`): si varias fuentes distintas publican el
mismo día un precio más alto, el precio va a subir.

## Lo que muestran los datos (base local, 2026-10-29)

Panel fijo de 15 canales con actividad en todas las semanas desde el 14-09:

| Semana | Anuncios con «arroz» | % del total | Mediana CUP/kg |
|---|---|---|---|
| 14-09 | 35 | 20,0 % | 700 |
| 21-09 | 35 | 13,3 % | 720 |
| 28-09 | 34 | 11,0 % | 764 |
| 05-10 | 21 | 5,7 % | 770 |
| 12-10 | 18 | 5,6 % | 785 |
| 19-10 | 23 | 7,3 % | 815 |

Los anuncios caen un 40 % y el precio sube un 16 %, en la dirección de la
hipótesis. **Es un solo episodio en 7 semanas: anécdota, no prueba.** Además el
precio ya subía antes de la caída. Alcanza para construir el sistema que mide;
no alcanza para publicar predicciones.

## Qué dice la literatura

- **Faltantes → inflación.** Cavallo y Kryvtsov midieron faltantes en tiendas
  online: los shocks *inesperados* de faltantes suben los precios en menos de
  3 meses, y más en bienes importados. Se usa el desvío respecto de lo
  esperado, no el nivel.
  [NBER w29209](https://nber.org/papers/w29209)
- **Anuncios como indicador adelantado.** En vivienda, los precios de los
  anuncios se adelantan 1–2 meses a los de las transacciones y reducen el error
  del pronóstico entre 5 % y 23 %.
  [CAMA 2025-45](https://ideas.repec.org/p/een/camaaa/2025-45.html),
  [Reusens y Vandenbergh 2025](https://cures.hias.hit-u.ac.jp/wp-content/uploads/2026/02/Reusens-Vandenbergh-2025-A-timely-house-price-index-for-Belgium-integrating-listings-and-transactions-for-improved-forecasting.pdf)
- **Disponibilidad en el monitoreo de mercados.** El PMA la usa como una de las
  9 dimensiones de su índice de funcionalidad de mercados y para alerta temprana.
  [WFP MFI](https://wfp.org/publications/market-functionality-index-mfi)
- **Detectar la caída.** Gráficos de control CUSUM o EWMA de un solo lado sobre
  conteos Poisson. Sin ajustar por estacionalidad dan demasiadas falsas alarmas.
  [Han et al.](https://scholar.korea.ac.kr/handle/2021.sw.korea/116737?mode=full),
  [conteos sindrómicos](https://emis.univie.ac.at/journals/HOA/JAMDS/Volume2009/512356.pdf)
- **Probar que predice.** Correlación cruzada con rezagos, causalidad de
  Granger y estudio de eventos (qué pasa con el precio 1–4 semanas después de
  cada alerta).

## Trampas propias de nuestros datos

1. **El conteo bruto sigue a la ingesta.** Pasamos de 2 a 44 canales: los
   anuncios de arroz subieron de 12 a 69 por semana sin que hubiera más arroz.
   Siempre se mide sobre un panel fijo de canales, versionado.
2. **El CUP se deprecia.** Sin deflactar por la tasa informal, todo «predice
   alza».
3. **La línea base es «sigue subiendo».** Una regla vale solo si le gana a la
   persistencia.
4. **No se pueden contar vendedores.** `sender_hash` es casi siempre nulo en los
   canales: se cuentan canales.
5. **Ruido del texto.** Buscar «arroz» atrapa combos de muchos productos,
   reposteos (`content_hash`) y pedidos de compra («compro arroz»), que
   invierten la señal.
6. **Factores locales.** La llegada del arroz de la libreta y el principio de
   mes mueven la frecuencia sin que haya escasez.

## Apartamientos

- **Congelamiento del 2026-10-21** («no se construye nada hasta que el mercado
  hable»): levantado para este módulo.
- **`spec.md` §9 y H0:** la capa predictiva es la Fase 3 y la bloquea H0. Este
  módulo se adelanta porque sus reglas son conteos sobre dato curado (correcto
  por construcción) y no dependen de la precisión del extractor. Hasta el
  slice 5 no sale de la vista interna.

## Diseño en una línea

Alcance inicial: **arroz en La Habana** (90 % del dato). Cada semana el módulo
calcula los indicadores, emite predicciones con reglas fijas, resuelve las
vencidas y se puntúa contra la persistencia. Las predicciones son de solo
anexar: no se editan después de conocer el resultado. Los umbrales son
configuración **sin valor por defecto** (§12.6): sin configurar, no predice.

## Tareas

Cada slice va en su propio worktree y se cierra antes de abrir el siguiente.
Los slices 1–3 tocan el esquema, así que siguen el flujo completo de
especificación de `CLAUDE.md`.

### Slice 1 — Decisión y especificación (solo documentos)

- [x] **T1 (opus)** Entrada en `decisions.md`: levanta el congelamiento y
  registra el apartamiento de §9 y H0. Hecha el 2026-10-29 junto con este plan.
- [ ] **T2 (opus)** Spec `docs/spec-slice-prediccion-v1.md`:
  - panel fijo de canales y su versión;
  - indicador: conteo semanal sin duplicados (`content_hash`), sin combos ni
    «compro», y su desvío respecto de lo esperado;
  - precio: mediana por kg deflactada por la tasa;
  - tabla de reglas v1, horizontes (2 y 4 semanas), línea base (persistencia)
    y forma de puntuar;
  - casos esperados escritos a mano antes del código.

### Slice 2 — Esquema e indicadores

- [ ] **T3 (opus)** Migración Alembic:
  - `availability_weekly`: producto, provincia, semana, conteo, esperado,
    desvío, versión del panel;
  - `forecasts` según §7.3, con regla, horizonte, dirección predicha, dirección
    de la línea base, resultado y fecha de resolución. Solo anexar.
- [ ] **T4 (sonnet)** Cálculo del indicador de disponibilidad desde
  `raw_ingestions`, con los tests primero.
- [ ] **T5 (sonnet)** Serie de precio semanal deflactada, reusando la
  conversión de §6.3 E y la mediana existente.

### Slice 3 — Reglas v1 y backtest

- [ ] **T6 (opus)** Tabla de reglas v1:
  - R1: caída de oferta → alza;
  - R2: consenso de fuentes con precio más alto → alza;
  - si ninguna se dispara, no hay predicción.
- [ ] **T7 (sonnet)** Backtest sobre agosto–octubre para elegir los umbrales
  iniciales; el resultado va a `decisions.md`.

### Slice 4 — Ejecución semanal y vista interna

- [ ] **T8 (sonnet)** `python -m saimc.prediction weekly` más un temporizador
  de systemd: calcula los indicadores, predice y resuelve las vencidas. No es
  una cola (decisión cerrada 3). Va en `docs/comandos.md`.
- [ ] **T9 (sonnet)** Pestaña interna en `/control`: indicador, predicciones
  abiertas y tasa de acierto contra la línea base, con su n. Mobile first,
  según `docs/DESIGN.md`.

### Slice 5 — Publicar (cuando los datos lo permitan)

- [ ] **T10 (opus)** Con unas 25–30 semanas y la regla ganándole a la
  persistencia: semáforo público (§6.7) con la etiqueta «Predicción
  exploratoria» (§11). Pide su propia decisión y ajustar el test que hoy
  prohíbe hablar de predicción en las páginas públicas.

### Después

Más productos (aceite, pollo) y provincias, detección con CUSUM y, con
volumen, TabPFN o Prophet (candidatos desde el 2026-08-08).
