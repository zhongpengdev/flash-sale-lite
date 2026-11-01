"""
Redis 客户端连接池封装 (Redis Client Pool)

【面试核心考点深度解析】：
1. 为什么必须使用连接池 (Connection Pool)？
   - 在秒杀瞬间可能出现数万 QPS。如果每个 HTTP 请求都新建并销毁 Redis TCP 连接，会导致操作系统短时间内产生
     大量 TIME_WAIT 状态套接字，耗尽端口甚至引发崩溃。
   - 连接池复用已建立的长连接，极大降低 TCP 三次握手与 TLS 开销。
2. 规范化 Redis Key 命名：
   - 采用 namespace:entity:id 规范（例如 flash_sale:stock:1），层级清晰且在各类 Redis GUI/监控工具中自然形成树状折叠。
3. redis.asyncio 单例最佳实践：
   - 全局维护一个共享的 Redis 异步客户端实例（内部托管连接池）。
   - 各协程并发安全借还连接，规避频繁实例化以及并发连接数耗尽 (MaxConnectionsError) 问题。
"""

from typing import Optional
import redis
import redis.asyncio as aioredis
from app.core.config import settings

# 异步 Redis 单例客户端 (托管异步连接池)
_async_redis_client: Optional[aioredis.Redis] = None

# 同步 Redis 连接池实例 (用于 Celery Worker)
_sync_redis_pool: Optional[redis.ConnectionPool] = None


# ------------------------------------------------------------------------------
# Redis 业务键规范常量生成函数
# ------------------------------------------------------------------------------
class RedisKeys:
    """Redis 键名统一规范生成器"""

    @staticmethod
    def stock_key(product_id: int) -> str:
        """秒杀商品库存缓存键: flash_sale:stock:{product_id}"""
        return f"flash_sale:stock:{product_id}"

    @staticmethod
    def users_set_key(product_id: int) -> str:
        """秒杀成功用户去重集合键 (Set): flash_sale:users:{product_id}"""
        return f"flash_sale:users:{product_id}"

    @staticmethod
    def order_status_key(order_id: str) -> str:
        """订单实时排队/支付状态缓存键: flash_sale:order_status:{order_id}"""
        return f"flash_sale:order_status:{order_id}"

    @staticmethod
    def rate_limit_key(identifier: str, action: str = "flash_sale") -> str:
        """限流滑动窗口键: rate_limit:{action}:{identifier}"""
        return f"rate_limit:{action}:{identifier}"


# ------------------------------------------------------------------------------
# FastAPI 异步 Redis 连接池管理
# ------------------------------------------------------------------------------
async def init_redis_pool() -> None:
    """服务启动时初始化全局异步 Redis 客户端与连接池"""
    global _async_redis_client
    if _async_redis_client is None:
        _async_redis_client = aioredis.from_url(
            settings.REDIS_URL,
            max_connections=500,  # 提高最大连接数以支撑高并发突发
            decode_responses=True,  # 自动解码为 utf-8 字符串
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
        )


async def close_redis_pool() -> None:
    """服务关闭时优雅释放连接池"""
    global _async_redis_client
    if _async_redis_client is not None:
        await _async_redis_client.aclose()
        _async_redis_client = None


async def get_redis() -> aioredis.Redis:
    """
    FastAPI 依赖注入使用的异步 Redis 客户端
    用法:
        redis_client: aioredis.Redis = Depends(get_redis)
    """
    global _async_redis_client
    if _async_redis_client is None:
        await init_redis_pool()
    return _async_redis_client


# ------------------------------------------------------------------------------
# Celery Worker 同步 Redis 客户端管理
# ------------------------------------------------------------------------------
def get_sync_redis() -> redis.Redis:
    """
    Celery Worker 同步任务中使用的 Redis 客户端 (连接池复用)
    """
    global _sync_redis_pool
    if _sync_redis_pool is None:
        _sync_redis_pool = redis.ConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=50,
            decode_responses=True,
            socket_timeout=5.0,
        )
    return redis.Redis(connection_pool=_sync_redis_pool)
