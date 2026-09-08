from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CONTROL_PLANE_",
        env_file=".env",
        extra="ignore",
    )

    environment: str = "development"
    database_url: str = "sqlite:///control-plane.db"
    log_level: str = "INFO"
    worker_poll_seconds: float = Field(default=1.0, gt=0)
    lease_seconds: int = Field(default=30, ge=5, le=3600)
    lease_heartbeat_seconds: float | None = Field(default=None, gt=0)
    evidence_window_size: int = Field(default=100, ge=10, le=10_000)
    high_risk_min_evidence_samples: int = Field(default=20, ge=1, le=10_000)
    worktree_root: Path = Path(".control-plane-worktrees")
    repository_registry_file: Path | None = None
    provider_policy_file: Path | None = None
    github_webhook_enabled: bool = False
    github_webhook_secret: SecretStr | None = None
    github_app_policy_file: Path | None = None
    oidc_policy_file: Path | None = None
    metrics_enabled: bool = False
    metrics_bearer_token: SecretStr | None = None
    dashboard_enabled: bool = True
    windsurf_mcp_enabled: bool = False
    windsurf_mcp_bearer_token: SecretStr | None = None
    windsurf_mcp_principal_id: str = "windsurf-cascade"
    windsurf_mcp_host: Literal["127.0.0.1", "localhost"] = "127.0.0.1"
    windsurf_mcp_port: int = Field(default=8010, ge=1024, le=65_535)


@lru_cache
def get_settings() -> Settings:
    return Settings()
