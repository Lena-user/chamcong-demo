from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str = "redis://redis:6379/0"
    jwt_secret: str
    jwt_expire_hours: int = 12
    allowed_hosts: str = "localhost,127.0.0.1,api,*.trycloudflare.com"

    # Operational values (see .env.example for the meaning of each one).
    timezone: str = "Asia/Ho_Chi_Minh"
    default_page_size: int = 20
    max_page_size: int = 100
    payroll_lock_key: int = 7300001
    payroll_job_timeout: int = 300
    standard_daily_minutes: int = 480
    unpaid_break_after_minutes: int = 360
    unpaid_break_minutes: int = 60
    night_shift_cutoff_hour: int = 12

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
