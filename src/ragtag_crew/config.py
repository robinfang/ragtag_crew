"""Configuration loaded from .env via pydantic-settings."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Telegram
    telegram_bot_token: str = ""
    allowed_user_ids: str = ""

    # LLM
    default_model: str = "openai/GLM-5.1"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openai_api_base: str = ""
    glm_api_key: str = ""
    glm_api_base: str = "https://open.bigmodel.cn/api/coding/paas/v4"

    # Agent
    working_dir: str = "."
    default_tool_preset: str = "coding"
    llm_timeout: int = 300
    llm_chunk_timeout: int = 30
    turn_timeout: int = 360
    bash_timeout: int = 30
    max_turns: int = 20
    skills_dir: str = "skills"
    project_context_file: str = "PROJECT.md"
    user_context_file: str = "USER.local.md"
    memory_index_file: str = "MEMORY.md"
    memory_dir: str = "memory"
    session_storage_dir: str = "data/sessions"
    session_ttl_hours: int = 72
    session_summary_trigger_messages: int = 18
    session_summary_recent_messages: int = 12
    session_summary_max_chars: int = 4000
    external_tool_timeout: int = 30
    mcp_servers_file: str = "mcp_servers.local.json"
    web_search_enabled: bool = False
    web_search_provider: str = "serper"
    web_search_api_url: str = "https://google.serper.dev/search"
    web_search_api_key: str = ""
    web_search_timeout: int = 15
    web_search_max_results: int = 5
    everything_enabled: bool = False
    everything_command: str = "es.exe"
    everything_timeout: int = 10
    everything_max_results: int = 50
    agent_browser_enabled: bool = False
    agent_browser_command: str = "agent-browser"
    browser_mode_default: str = "isolated"
    browser_profile_dir: str = "data/browser/isolated"
    browser_default_timeout: int = 30
    browser_headed: bool = True
    browser_allowed_domains: str = ""
    browser_attached_enabled: bool = False
    browser_attached_require_confirmation: bool = True
    browser_attached_cdp_url: str = ""
    browser_attached_auto_connect: bool = False

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    def get_allowed_user_ids(self) -> set[int]:
        if not self.allowed_user_ids.strip():
            return set()
        return {int(uid.strip()) for uid in self.allowed_user_ids.split(",") if uid.strip()}


settings = Settings()
