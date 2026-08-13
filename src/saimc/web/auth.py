"""HTTP Basic para la cola de curaduría (decisions.md, 2026-08-13).

Protege únicamente las rutas del operador (`/curation/*`, `/evidence/{id}`).
`/control`, el dashboard público, no depende de esto y sigue sin auth.
"""

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from saimc.config import get_settings

_security = HTTPBasic()


def require_operator(credentials: HTTPBasicCredentials = Depends(_security)) -> None:
    settings = get_settings()
    if not settings.curation_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SAIMC_CURATION_PASSWORD no configurado",
        )

    valid_username = secrets.compare_digest(credentials.username, settings.curation_username)
    valid_password = secrets.compare_digest(credentials.password, settings.curation_password)
    if not (valid_username and valid_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas",
            headers={"WWW-Authenticate": "Basic"},
        )
