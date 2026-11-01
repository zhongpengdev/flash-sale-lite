"""
系统核心配置模块 (Core Configuration)
基于 Pydantic-Settings 实现环境变量加载与类型校验。

【面试考点解读】：
1. 为什么用 pydantic-settings？
   - 遵循 12-Factor 原则（配置存储在环境中），集中管理环境变量。
   - 自动提供强类型转换与缺省值回退，并在服务启动时快速失败（Fail-Fast），避免运行中才报配置缺失错误。
2. 双连接串设计：
   - FastAPI 接口层走纯异步驱动（asyncpg），提高并发吞吐。
   - Celery Worker 在多进程模型中稳定运行，使用同步驱动（psycopg2），规避事件循环跨进程继承导致的死锁问题。
"""

from functools import lru_cache
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 项目元信息
    PROJECT_NAME: str = "Flash Sale Lite"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    # PostgreSQL 配置
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "flash_sale"

    # Redis 配置
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: Optional[str] = None

    # Celery 任务队列配置
    # 注意：Broker 与 Result 使用不同 DB，避免任务数据与缓存数据冲突
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # 秒杀业务相关配置
    # 订单未支付自动超时取消时间 (秒)，默认 15 分钟
    ORDER_EXPIRE_SECONDS: int = 900
    # 默认限流：单个 IP/用户 每秒最大请求数
    RATE_LIMIT_PER_SECOND: int = 5

    @property
    def ASYNC_DATABASE_URL(self) -> str:
        """SQLAlchemy 异步数据库连接串 (用于 FastAPI 接口层)"""
        pwd = f":{self.POSTGRES_PASSWORD}" if self.POSTGRES_PASSWORD else ""
        return f"postgresql+asyncpg://{self.POSTGRES_USER}{pwd}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    @property
    def SYNC_DATABASE_URL(self) -> str:
        """SQLAlchemy 同步数据库连接串 (用于 Celery Worker 异步落库)"""
        pwd = f":{self.POSTGRES_PASSWORD}" if self.POSTGRES_PASSWORD else ""
        return f"postgresql+psycopg2://{self.POSTGRES_USER}{pwd}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    @property
    def REDIS_URL(self) -> str:
        """Redis 连接串"""
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    """
    使用 lru_cache 缓存配置实例，避免每次调用重复解析 .env 文件与系统环境变量。
    """
    return Settings()


settings = get_settings()
