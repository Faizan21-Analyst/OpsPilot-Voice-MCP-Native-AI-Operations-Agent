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


class LiteLLMSettings(BaseSettings):
    num_retries: int = 2
    timeout_s: float = 20.0


class GroqSettings(BaseSettings):
    api_key: str = Field(..., description="Groq API key")
    model: str = "llama-3.3-70b-versatile"
    timeout_s: float = 15.0

class DatabaseSettings(BaseSettings):
    url: str = "sqlite+aiosqlite:///./data/opspilot.db"

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