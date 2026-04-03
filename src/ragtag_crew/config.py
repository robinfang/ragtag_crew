"""Configuration loaded from .env via pydantic-settings."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Telegram
    telegram_bot_token: str = ""
    allowed_user_ids: str = ""

    # LLM
    default_model: str = "anthropic/claude-sonnet-4-20250514"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    glm_api_key: str = ""
    glm_api_base: str = "https://open.bigmodel.cn/api/paas/v4"

    # Agent
    working_dir: str = "."
    default_tool_preset: str = "coding"
    bash_timeout: int = 30
    max_turns: int = 20

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    def get_allowed_user_ids(self) -> set[int]:
        if not self.allowed_user_ids.strip():
            return set()
        return {int(uid.strip()) for uid in self.allowed_user_ids.split(",") if uid.strip()}


settings = Settings()
