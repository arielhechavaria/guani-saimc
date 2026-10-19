# Seguridad de la capa pública

Qué protege el código y qué le toca al servidor. Decisión de origen:
`docs/decisions.md`, 2026-10-19.

## En el código

- Cabeceras de seguridad en toda respuesta (`web/security_headers.py`): `nosniff`,
  `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy`, `Permissions-Policy` y una CSP
  parcial (`frame-ancestors`, `object-src`, `base-uri`, `form-action`). HSTS solo
  cuando la petición llegó por HTTPS (directa o con `X-Forwarded-Proto`).
- Rutas del operador con HTTP Basic; puerta del sitio con cookie firmada;
  webhook de WAHA con HMAC-SHA512; `/docs` y `openapi.json` apagados.
- `/datos.csv` recorta a 30 días.

## En el servidor (nginx): lo que NO hace el código

El código no limita pedidos. Sin esto, `/anuncio/{id}` (ids secuenciales) se
recorre entero y el login Basic del operador admite fuerza bruta.

```nginx
# en http { ... }
limit_req_zone $binary_remote_addr zone=saimc_pub:10m  rate=30r/m;
limit_req_zone $binary_remote_addr zone=saimc_csv:10m  rate=6r/m;
limit_req_zone $binary_remote_addr zone=saimc_op:10m   rate=10r/m;
limit_req_status 429;

# en server { ... }  (ajusta el puerto de uvicorn)
location /anuncio/ {            # el más fácil de recorrer
    limit_req zone=saimc_pub burst=20 nodelay;
    proxy_pass http://127.0.0.1:8000;
    include proxy_params;
}
location = /datos.csv {
    limit_req zone=saimc_csv burst=3 nodelay;
    proxy_pass http://127.0.0.1:8000;
    include proxy_params;
}
location ~ ^/(curation|control|catalog|marketing|docs|evidence) {
    limit_req zone=saimc_op burst=20 nodelay;   # freno a la fuerza bruta
    proxy_pass http://127.0.0.1:8000;
    include proxy_params;
}
location /webhooks/ {           # WAHA manda ráfagas: sin límite, ya exige firma HMAC
    proxy_pass http://127.0.0.1:8000;
    include proxy_params;
}
location / {
    limit_req zone=saimc_pub burst=60 nodelay;
    proxy_pass http://127.0.0.1:8000;
    include proxy_params;
}
```

`proxy_params` debe pasar `X-Forwarded-For` y `X-Forwarded-Proto` (el límite de
intentos del formulario `/acceso` y HSTS dependen de ellos). `/webhooks/whatsapp` queda fuera del límite a propósito: WAHA envía ráfagas
(backfill) y la ruta ya exige firma.

## Lista antes de apagar la puerta del sitio

1. Todo el tráfico por HTTPS; el puerto de uvicorn no está expuesto fuera de `127.0.0.1`.
2. Solo 80 y 443 abiertos hacia fuera (`ufw status`).
3. `SAIMC_SITE_URL` fijado y `SAIMC_SITE_PASSWORD` distinta de la del operador.
4. El `limit_req` de arriba aplicado: `curl -s -o /dev/null -w '%{http_code}\n' https://<sitio>/anuncio/1`
   repetido 80 veces seguidas tiene que acabar en `429`.
5. `curl -sI https://<sitio>/` muestra `strict-transport-security` y `x-content-type-options`.

## Límites que se aceptan

El scraping lento (por debajo del límite) no se frena; lo que lo desincentiva es
el tope del CSV y la regla de `/uso-de-datos`. La CSP no trae `script-src`: las
plantillas usan código en línea.
