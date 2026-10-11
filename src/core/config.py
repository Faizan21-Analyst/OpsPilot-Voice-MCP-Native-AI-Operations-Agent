"""
App configuration. Reads from .env file and environment variables.
"""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv
load_dotenv()

class GeminiSettings(BaseSettings):
    api_key: str
    model: str = "gemini/gemini-2.5-flash"
    # Optional second Gemini model, used only if everything before it failed. Its quota is counted separately from
    # the main model's. Empty = not used.
    fallback_model: str = ""


class LiteLLMSettings(BaseSettings):
    num_retries: int = 2
    timeout_s: float = 20.0
    # Most tokens a Groq answer may contain. Groq refuses a request whose POSSIBLE output is above the per-minute
    # output limit (1000 on some free-tier models), so keep this below that limit. 0 = no cap.
    max_output_tokens: int = 700
    # How long a rate-limited Groq key is skipped before it is tried again.
    cooldown_s: float = 60.0


class GroqSettings(BaseSettings):
    api_key: str = Field(..., description="Groq API key")
    # Extra keys, ideally from different Groq organisations: each organisation has its own rate limits, so the
    # router spreads requests over them and skips a key while it is rate limited. All optional.
    api_key2: str = ""
    api_key3: str = ""
    api_key4: str = ""
    model: str = "llama-3.3-70b-versatile"
    # Optional second Groq model (same keys), tried after the main model. Empty = not used.
    fallback_model: str = ""
    timeout_s: float = 15.0

    @property
    def api_keys(self) -> list[str]:
        """Every configured key, without blanks or duplicates, main key first."""
        keys: list[str] = []
        for key in (self.api_key, self.api_key2, self.api_key3, self.api_key4):
            key = (key or "").strip()
            if key and key not in keys:
                keys.append(key)
        return keys

class DatabaseSettings(BaseSettings):
    url: str = "sqlite+aiosqlite:///./data/opspilot.db"

class AuthSettings(BaseSettings):
    jwt_secret: str
    token_expire_minutes: int = 480  # 8 hours

class MCPServersSettings(BaseSettings):
    docs_url: str = "http://127.0.0.1:8001/mcp"
    sql_url: str = "http://127.0.0.1:8002/mcp"
    ops_url: str = "http://127.0.0.1:8003/mcp"

class AppSettings(BaseSettings):
    env: str = "dev"
    log_level: str = "INFO"
    groq: GroqSettings
    gemini: GeminiSettings
    litellm: LiteLLMSettings
    auth: AuthSettings
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    mcp_servers: MCPServersSettings = Field(default_factory=MCPServersSettings)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
    )



def load_settings() -> AppSettings:
    """Call this once at startup. Raises clearly if config is broken."""
    return AppSettings()