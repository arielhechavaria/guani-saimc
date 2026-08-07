"""Configuración leída del entorno.

`get_settings` está cacheada porque `Settings()` lee el archivo `.env` en cada
instanciación. La caché la convierte en un singleton perezoso, que es el patrón
habitual en FastAPI: el objeto se arma la primera vez que alguien lo pide y las
llamadas siguientes reciben el mismo. En los tests se limpia con
`get_settings.cache_clear()`.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SAIMC_",
        env_file=".env",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://saimc:saimc@localhost:5432/saimc"

    # Directorio observado donde el operador deposita capturas y textos.
    inbox_dir: Path = Path("inbox")

    # Evidencia preservada, nombrada por hash de contenido.
    raw_store_dir: Path = Path("data/raw")


@lru_cache
def get_settings() -> Settings:
    return Settings()
