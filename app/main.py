"""
FastAPI 核心入口与应用生命周期管理 (Main Application)

【面试核心考点深度解析】：
1. 为什么使用 lifespan 上下文管理器替代旧版 on_event("startup")？
   - FastAPI 0.93+ 全面推荐使用 ASGI 标准的 lifespan 异步上下文管理器。
   - 它在一个清晰的代码块中同时管理系统资源的初始化（数据库连接池、Redis 连接池预热）
     和优雅下线（Graceful Shutdown，关闭连接池、释放文件描述符），逻辑内聚，杜绝遗漏。
2. 架构分层规范：
   - main.py 仅负责路由挂载、中间件注册与全局生命周期编排，严禁混入业务逻辑代码。
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import init_db, async_engine
from app.core.redis import init_redis_pool, close_redis_pool
from app.api.v1.api import api_router

# 配置全局日志格式
logging.basicConfig(
    level=logging.INFO if not settings.DEBUG else logging.DEBUG,
    format="%(asctime)s | %(levelname)-7s | %(name)s:%(lineno)d - %(message)s",
)
logger = logging.getLogger("flash_sale_lite")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 异步生命周期管理
    - startup: 建立数据库表结构，初始化 Redis 异步连接池
    - shutdown: 优雅关闭 Redis 连接池与数据库引擎
    """
    logger.info(">>> [服务启动] 正在初始化数据库表结构...")
    await init_db()
    logger.info(">>> [服务启动] 正在建立 Redis 异步连接池...")
    await init_redis_pool()
    logger.info(">>> [服务启动完成] Flash Sale Lite 系统已就绪！")

    yield

    logger.info("<<< [服务关闭] 正在释放 Redis 连接池...")
    await close_redis_pool()
    logger.info("<<< [服务关闭] 正在关闭 SQLAlchemy 数据库连接池...")
    await async_engine.dispose()
    logger.info("<<< [服务关闭完成] 资源释放完毕，安全退出。")


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="轻量级高并发秒杀与订单履约系统 (Flash Sale Lite) - 教学与实战演示",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# 跨域资源共享 (CORS) 中间件配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载 API v1 路由汇总
app.include_router(api_router, prefix="/api/v1")


@app.get("/", tags=["系统监控"])
async def root():
    """根路径引导接口"""
    return {
        "project": settings.PROJECT_NAME,
        "status": "online",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["系统监控"], status_code=status.HTTP_200_OK)
async def health_check():
    """
    系统健康检查接口
    用于容器健康检查探针 (Liveness/Readiness Probe)
    """
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "healthy", "environment": settings.ENVIRONMENT},
    )
