# Decisiones

Registro de anexado. No se reescriben ni se borran entradas viejas. Si una
decisión cambia, se agrega una entrada nueva que diga a cuál reemplaza.

Existe como evidencia de las condiciones bajo las que se tomó cada medición de H0,
y como fuente para validar decisiones tomadas antes. No es un registro general:
solo se anota lo que, mirando el código, alguien concluiría razonablemente que
está mal.

---

## 2026-08-06 — raw_ingestions se aparta del DDL del whitepaper en dos puntos

Contexto: el whitepaper §7.1 (línea 1061) trae el `CREATE TABLE` de
`raw_ingestions` sin ninguna restricción `NOT NULL` y con `TIMESTAMP` sin zona
horaria. Implementado tal cual, `content_hash` sería nulable bajo un índice único,
y en PostgreSQL varios `NULL` no colisionan entre sí: la deduplicación de capturas
repetidas —que es para lo que ese índice existe— no funcionaría.

Decisión: se agregó `NOT NULL` a `id`, `source_type`, `file_path`, `content_hash`,
`content_type`, `processing_status`, `captured_at` y `created_at`. Quedan nulables
`source_reference` y `original_text`, que sí se completan más tarde durante la
curaduría. Las dos marcas de tiempo pasaron a `TIMESTAMPTZ`.

Descartado: copiar el DDL literal. Habría dado un índice único inútil y fechas
ambiguas en un entorno WSL donde la zona del sistema y la del servidor no tienen
por qué coincidir.

Consecuencia: el esquema real y el whitepaper ya no son idénticos. La diferencia
está acotada a restricciones y zona horaria; nombres de tabla, de columnas, tipos
base y el nombre del índice (`idx_raw_ingestions_hash`) se mantienen para que la
comparación siga siendo directa. Anotado en `docs/whitepaper-map.md`.

---

## 2026-08-06 — Diferenciación frente a elTOQUE reescrita sobre premisas verificables

Contexto: la v3.4 presentaba a elTOQUE como «caja negra» y su transparencia como
«opaca». La metodología de la TRMI es pública —recolección por bots, NLP, mediana
sobre ventana de 24 h con filtrado de extremos, EMA para monedas de baja densidad—.
Una diferenciación apoyada en esa premisa se cae en la primera pregunta de un
inversor que abra el sitio del competidor.

Decisión: el posicionamiento (§5) se reconstruye sobre cuatro ejes verificables:
objeto medido (bienes vs. divisas), granularidad (provincial vs. nacional),
naturaleza del problema técnico (extracción **más** resolución de entidades), y
trazabilidad por dato. Se separa además «elTOQUE — TRMI» de «elTOQUE Markets» en el
mapa de competidores: son productos distintos y solo el segundo compite con SAIMC.

Descartado: sostener «ellos son opacos». También se descartó la formulación de la
investigación, que abandonaba el eje de trazabilidad. Que la metodología sea pública
no implica que cada precio publicado sea rastreable hasta el anuncio que lo originó;
ese eje es el más fuerte que SAIMC tiene y se conserva.

Consecuencia: la Transparency Layer (§6.4) y el Data Lineage Framework (§6.5) dejan
de ser una funcionalidad más y pasan a ser el sustento del posicionamiento. Si se
recortan, se cae la diferenciación completa.

---

## 2026-08-06 — El tipo de cambio informal es una dependencia externa declarada

Contexto: los anuncios llegan en CUP, USD, EUR y MLC. El whitepaper extraía `currency`
y `prices_history` tenía la columna, pero no existía en ninguna parte una política de
conversión. Una serie temporal comparable exige base monetaria única, y esa base sale
de una referencia que SAIMC no produce.

Decisión: se adopta CUP como moneda base de las series publicadas y se declara la TRMI
de elTOQUE como fuente primaria de conversión, con fuente de respaldo obligatoria,
política de degradación (última tasa conocida, ventana de validez máxima, marcado
explícito de serie con tasa vencida) y atribución visible. `prices_history` suma
`price_cup`, `exchange_rate`, `exchange_rate_date` y `exchange_rate_source`
(§6.3 componente E y §7.2).

Descartado: guardar solo el precio convertido. Sin la tasa histórica almacenada, un
cambio retroactivo en la fuente reescribe la serie entera en silencio y rompe la
reproducibilidad que sostiene §6.5.

Consecuencia: SAIMC hereda las propiedades estadísticas y los riesgos de
disponibilidad de su fuente de tipo de cambio. Los cuatro campos entran en el diseño
de `prices_history` desde el primer modelo: agregarlos después obliga a reprocesar
registros crudos.

---

## 2026-08-06 — La manipulación adversarial entra como riesgo de primer orden

Contexto: el riesgo 10.1 cubría error, ruido y spam —fuentes independientes entre sí—.
No había nada sobre manipulación deliberada y coordinada. El modelo de ataque está
públicamente articulado contra el sistema metodológicamente más cercano: en diciembre
de 2025 se difundió un análisis que señalaba que cuatro grupos de Telegram aportaban
más de la mitad de la muestra de la TRMI. Quien controla la mitad más uno de las
observaciones de una celda determina su mediana.

Decisión: nuevo riesgo §10.7 con cuatro contramedidas —umbral mínimo de muestra (C1),
estimadores robustos (C2), índice de control paralelo (C3), detección de coordinación
(C4)— y alcance declarado: elevar el costo del ataque y detectarlo, no alcanzar
inmunidad.

Descartado: numerarlo como 10.2 y correr el resto, que es donde la investigación lo
ubicaba por importancia. El §10.6 está referenciado desde `CLAUDE.md`, `.gitignore`,
`README.md` y `src/saimc/ingestion/inbox.py`; renumerar rompía todas esas referencias
por una ganancia de orden de lectura. Se compensa con un puntero desde 10.1.

Consecuencia: SAIMC es estructuralmente más vulnerable que un sistema de tipo de
cambio —matriz en vez de valor único, densidad por celda mucho menor—. C3 debe estar
operativo **antes** de habilitar el botón «subir dato» de la Fase 2, no después.

---

## 2026-08-06 — Umbral de densidad como condición de publicación por provincia

Contexto: la sobreextensión geográfica es una causa documentada de fracaso en sistemas
de información de mercado de primera generación. La arquitectura permite ingerir todas
las provincias desde el día 1, y eso es precisamente la trampa: cobertura nacional con
densidad insuficiente produce series ruidosas y manipulables, y hunde la credibilidad
del sistema entero por el desempeño de sus celdas más débiles.

Decisión: §9.0. Una provincia se publica solo tras superar y sostener un umbral N de
observaciones válidas, verificado sobre múltiples categorías. Por debajo, ingesta sin
publicación, con el estado de cada provincia visible en la interfaz. Los números de
provincias de las Fases 1 y 2 pasan a ser techos de ingesta, no metas de publicación.

Descartado: aplicar la restricción literal desde el mes 1. Con 500 registros sobre
3–5 productos y 2 provincias, ninguna celda alcanza densidad seria, y el dashboard de
Fase 1 quedaría vacío —contradiciendo §3, que dice que H0 no bloquea el lanzamiento
público—. La conciliación separa las dos razones del umbral: la estadística rige desde
el día 1 (tamaño de muestra visible, sin titular de precio); la adversarial rige desde
la Fase 2, cuando se abre la ingesta comunitaria y desaparece la barrera del operador
humano que verifica cada registro contra su evidencia.

Consecuencia: las Fases 0 y 1 son el período de calibración de N, no el período en que
N se aplica. Y queda una restricción vinculante sobre el próximo slice: incorporar una
provincia debe ser configuración, no desarrollo.

---

## 2026-08-06 — Se publica toda la metodología, con una reserva acotada y declarada

Contexto: el whitepaper comprometía «reglas públicas y lógica de negocio abierta» sin
calificar. La contramedida C4 —detección de coordinación— es antifraude: sus umbrales
de sincronía, similitud textual y puntuación de fuentes pierden eficacia al publicarse,
igual que las reglas de detección de fraude en pagos o los filtros antispam.

Decisión: §6.4.1. Se publica todo —fórmulas, umbrales de validación y densidad, reglas
del validador, criterios de normalización, ventanas de cálculo, métricas de calidad,
código y datos agregados— aplicando el principio de Kerckhoffs: si publicar los
umbrales comprometiera la integridad, el problema sería el diseño. Se reservan
únicamente los parámetros de C4, y la existencia de esa reserva se declara
públicamente junto con su motivo.

Descartado: presentar esto como «resolución de un ítem abierto», que es como lo
planteaba la investigación. No había ítem abierto: el compromiso previo era sin
reservas. Esto **acota** el principio 1 de Transparencia Radical. Registrarlo como
aclaración habría escondido una reducción de alcance.

Consecuencia: cualquier ampliación futura de lo reservado necesita entrada nueva acá y
anuncio público. El principio 1 lleva ahora un puntero explícito a §6.4.1.
