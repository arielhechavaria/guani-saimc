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
