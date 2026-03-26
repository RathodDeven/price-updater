from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "price-updater-backend"
    app_env: str = "dev"
    debug: bool = True
    output_root: Path = Path("./runtime_outputs")
    tmp_root: Path = Path("./runtime_tmp")

    openai_api_key: str = ""
    openai_model: str = "gpt-5.4"

    google_application_credentials: str = ""
    google_cloud_project: str = ""
    google_cloud_location: str = "us"
    google_docai_processor_id: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
settings.output_root.mkdir(parents=True, exist_ok=True)
settings.tmp_root.mkdir(parents=True, exist_ok=True)
