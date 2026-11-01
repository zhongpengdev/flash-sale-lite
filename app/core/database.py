"""
数据库引擎与会话管理 (Database Engine & Session Management)

【面试核心考点】：
1. 为什么 FastAPI 使用异步 asyncpg，而 Celery 使用同步 psycopg2？
   - FastAPI 是基于 ASGI 的异步事件循环架构，使用 asyncpg 可以在等待 I/O 时让出 CPU，大幅提升接口并发能力。
   - Celery 采用 prefork 多进程模型。如果子进程继承了主进程的 asyncio 事件循环与连接池，会导致跨进程共享套接字
     产生著名的 "RuntimeError: Task got future attached to a different loop" 或连接竞争。
     采用独立的同步连接池是生产级 Celery 任务最稳妥可靠的解法。
2. 数据库连接池参数设计：
   - pool_size: 维持的连接数
   - max_overflow: 峰值允许额外创建的连接数
   - pool_pre_ping: 在每次借出连接前发送探活心跳，防止因数据库端超时关闭连接导致查询报错。
"""

from typing import AsyncGenerator
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.config import settings
from app.models import Base

# ------------------------------------------------------------------------------
# 1. FastAPI 异步数据库引擎与会话池 (基于 asyncpg)
# ------------------------------------------------------------------------------
async_engine = create_async_engine(
    settings.ASYNC_DATABASE_URL,
    echo=False,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=3600,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI 依赖注入使用的异步数据库会话生成器
    接口函数使用 `db: AsyncSession = Depends(get_db)` 即可获取独立会话，并在请求结束时自动关闭。
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ------------------------------------------------------------------------------
# 2. Celery Worker 同步数据库引擎与会话池 (基于 psycopg2)
# ------------------------------------------------------------------------------
sync_engine = create_engine(
    settings.SYNC_DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,
    pool_recycle=3600,
)

SyncSessionLocal = sessionmaker(
    bind=sync_engine,
    class_=Session,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


@contextmanager
def get_sync_db():
    """
    Celery 异步落库任务使用的上下文管理器
    用法:
        with get_sync_db() as db:
            db.add(order)
            db.commit()
    """
    session = SyncSessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ------------------------------------------------------------------------------
# 3. 数据库表初始化辅助函数
# ------------------------------------------------------------------------------
async def init_db() -> None:
    """
    服务启动时自动检查并创建所有尚未创建的表结构 (通过 Base.metadata)
    """
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
