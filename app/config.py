from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://payments:payments@localhost:5432/payments"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    api_key: str = "dev-api-key"
    log_level: str = "INFO"

    outbox_poll_interval: float = 1.0
    outbox_batch_size: int = 50

    # эмуляция эквайринга из ТЗ
    gateway_delay_min: float = 2.0
    gateway_delay_max: float = 5.0
    gateway_success_rate: float = 0.9

    webhook_timeout: float = 10.0
    webhook_attempts: int = 3


settings = Settings()
