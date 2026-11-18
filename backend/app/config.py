import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./kiwi_music.db",
        description="Async database connection string"
    )
    YUKISBOX_BASE_URL: str = Field(
        default="https://yukiapi.site",
        description="Base URL for Yukisbox storage"
    )
    YUKISBOX_TIMEOUT: int = Field(
        default=300,
        description="Yukisbox request timeout in seconds"
    )
    JIOSAAVN_BASE_URL: str = Field(
        default="",
        description="Optional upstream JioSaavn API base URL"
    )
    INTERNAL_API_SECRET: str = Field(
        default="kiwi_secret_change_me_in_production",
        description="Shared secret for internal management endpoints"
    )
    REDIS_URL: str | None = Field(
        default=None,
        description="Optional Redis URL for hot caching"
    )
    MAX_UPLOAD_CONCURRENCY: int = Field(
        default=3,
        description="Maximum concurrent uploads to Yukisbox"
    )
    MAX_DOWNLOAD_SIZE_MB: int = Field(
        default=100,
        description="Maximum allowed audio download size in megabytes"
    )
    TEMP_DIR: str = Field(
        default="./temp",
        description="Directory for temporary chunked storage"
    )
    LIFETIME_QUOTA: int = Field(
        default=200,
        description="Lifetime successful requests quota per API key"
    )
    RUST_MEDIA_PORT: int = Field(
        default=8088,
        description="Internal port for Rust Axum streaming engine"
    )
    RUST_MEDIA_ENGINE_URL: str = Field(
        default="http://127.0.0.1:8088",
        description="Internal base URL for Rust Axum media engine"
    )

    @field_validator("DATABASE_URL")
    @classmethod
    def assemble_db_connection(cls, v: str) -> str:
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgresql://") and not v.startswith("postgresql+asyncpg://"):
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)

        if "sqlite" in v:
            return v

        from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit
        split = urlsplit(v)
        params = dict(parse_qsl(split.query))

        # Convert sslmode to ssl for asyncpg
        if "sslmode" in params:
            sslmode_val = params.pop("sslmode")
            if sslmode_val in ("require", "prefer", "allow", "verify-ca", "verify-full"):
                params["ssl"] = "require"

        # Remove libpq parameters unsupported by asyncpg
        unsupported_libpq_keys = {"channel_binding", "gssencmode", "target_session_attrs", "options"}
        for key in unsupported_libpq_keys:
            params.pop(key, None)

        new_query = urlencode(params)
        return urlunsplit((split.scheme, split.netloc, split.path, new_query, split.fragment))

settings = Settings()

