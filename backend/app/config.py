from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    llm_provider: str = "auto"  # auto | gemini | github_models

    gemini_api_key: str = ""
    gemini_api_keys: str = ""  # comma-separated list of API keys for rotation
    gemini_model: str = "gemini-3-pro"
    gemini_fallback_models: str = ""  # No fallbacks for free tier entitlement
    gemini_rpm_limit: int = 3
    gemini_rpd_limit: int = 1500
    database_url: str = "sqlite+aiosqlite:///./testgen.db"
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    log_level: str = "INFO"

    def get_all_api_keys(self) -> list[str]:
        """Returns all available API keys (from both GEMINI_API_KEY and GEMINI_API_KEYS)."""
        keys = set()
        if self.gemini_api_key:
            keys.add(self.gemini_api_key.strip())
        if self.gemini_api_keys:
            for k in self.gemini_api_keys.split(","):
                k = k.strip()
                if k:
                    keys.add(k)
        return list(keys)

    # Gemini generation params
    gemini_temperature: float = 0.3
    gemini_top_p: float = 0.8
    gemini_max_output_tokens: int = 8192

    # GitHub Models generation params
    github_models_token: Optional[str] = None
    github_models_org: Optional[str] = None
    github_models_model: str = "openai/gpt-4.1-mini"
    github_models_api_base: str = "https://models.github.ai"
    github_models_api_version: str = "2022-11-28"
    github_models_rpm_limit: int = 2
    github_models_rpd_limit: int = 150
    github_models_max_output_tokens: int = 2048
    github_models_temperature: float = 0.0
    github_models_max_retries: int = 2
    github_models_enable_gap_fill: bool = False

    # GitHub Context
    github_token: Optional[str] = None
    github_app_id: Optional[str] = None
    github_client_id: Optional[str] = None
    github_client_secret: Optional[str] = None
    github_callback_url: str = "http://localhost:5173/github-callback.html"

    class Config:
        env_file = (".env", ".env.github-models")
        env_file_encoding = "utf-8"


def get_settings() -> Settings:
    """Fresh settings every call — no cache so .env changes take effect on reload."""
    return Settings()
