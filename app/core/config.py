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

    # Password hashing (argon2-cffi defaults). Existing hashes keep verifying: params live in the hash.
    argon2_time_cost: int = 2
    argon2_memory_kib: int = 19456
    argon2_parallelism: int = 1
    hash_concurrency: int = 2  # simultaneous hashes per process (each one costs argon2_memory_kib)
    login_max_waiting: int = 200  # logins queued for a hash per process before answering 503

    # Overload handling
    retry_after_seconds: int = 2
    db_statement_timeout_ms: int = 5000
    db_lock_timeout_ms: int = 2000
    db_idle_in_tx_timeout_ms: int = 10000
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_pool_timeout: int = 10
    worker_db_pool_size: int = 2
    arq_max_jobs: int = 2
    uvicorn_workers: int = 3
    db_reserved_connections: int = 10

    access_log: bool = True

    rate_limit_enabled: bool = True
    rate_limit_fail_open: bool = True
    rate_login_per_min: int = 20
    rate_write_per_min: int = 600

    auth_cache_ttl_seconds: int = 30  # 0 disables the cache
    auth_cache_max: int = 10000

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
