"""
基于 Redis 的滑动窗口限流依赖项 (Rate Limiter)

【面试高频考点深度解析】：
1. 为什么限流是高并发秒杀系统的第一道防线？
   - 绝大多数秒杀流量是无效刷子或脚本（爬虫、连点器）。在网关或框架入口层（FastAPI 依赖注入）
     拦截超额流量，可以避免大量请求打到后端的 Redis Lua 甚至数据库，保护核心系统不被击垮。
2. 常用限流算法对比（面试必问）：
   - 固定窗口 (Fixed Window):
     实现简单（INCR + EXPIRE），但存在“临界突发”缺陷。例如限制 1秒5次，若在 0.99 秒打入 5 次，1.01 秒又打入 5 次，
     在 0.02 秒内瞬间通过了 10 次请求，无法平滑流量。
   - 滑动窗口 (Sliding Window Log):
     通过 Redis 的 ZSET（有序集合）记录每次请求的时间戳毫秒数。每次请求来时：
     1) 移除当前时间窗口之外的旧时间戳 (`ZREMRANGEBYSCORE`)；
     2) 统计当前窗口内的请求数 (`ZCARD`)；
     3) 若请求数小于限制阈值，将本次请求时间戳插入 ZSET (`ZADD`)，放行；
     4) 否则抛出 HTTP 429 异常拦截。
     保证在任意 1 秒滑动周期内绝对不会超过 5 次请求。
   - 令牌桶 (Token Bucket) / 漏桶 (Leaky Bucket):
     更适合做整机平滑出口流量整形，但实现相对复杂（需引入脚本或 Celery 周期填充）。滑动窗口在单机/分布式接口防刷中兼顾了精度与易维护性。
"""

import time
import json
from typing import Optional
from fastapi import Request, HTTPException, status
import redis.asyncio as aioredis
from app.core.redis import get_redis, RedisKeys


class RateLimiter:
    """
    可配置的滑动窗口限流依赖项类
    用法:
        @router.post("/buy", dependencies=[Depends(RateLimiter(times=5, seconds=1))])
    """

    def __init__(self, times: int = 5, seconds: int = 1, action: str = "flash_sale"):
        """
        :param times: 时间窗口内允许的最大请求数 (默认 5 次)
        :param seconds: 时间窗口大小，单位秒 (默认 1 秒)
        :param action: 业务动作名称，用于区分不同的接口限流键空间
        """
        self.times = times
        self.seconds = seconds
        self.action = action

    async def __call__(self, request: Request) -> None:
        """
        FastAPI 依赖解析入口
        优先尝试提取 user_id 进行用户级限流；提取不到则降级为客户端 IP 限流。
        """
        # 1. 尝试获取用户标识 (Header / Query / JSON Body)
        identifier: Optional[str] = None

        # 优先从 Header 读取 (例如 X-User-Id)
        user_header = request.headers.get("x-user-id")
        if user_header:
            identifier = f"user:{user_header}"

        # 若 Header 无用户 ID，尝试从请求体 JSON 获取 (不消费 stream，通过 request.body 预读)
        if not identifier and request.method in ("POST", "PUT", "PATCH"):
            try:
                body_bytes = await request.body()
                if body_bytes:
                    body_json = json.loads(body_bytes.decode("utf-8"))
                    if isinstance(body_json, dict) and "user_id" in body_json:
                        identifier = f"user:{body_json['user_id']}"
            except Exception:
                pass  # 请求体无法解析为 JSON 时静默忽略，降级到 IP

        # 若仍未获取到用户标识，则使用客户端真实 IP
        if not identifier:
            client_ip = request.client.host if request.client else "unknown"
            # 兼容反向代理标头 X-Forwarded-For
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                client_ip = forwarded.split(",")[0].strip()
            identifier = f"ip:{client_ip}"

        # 2. 获取全局 Redis 客户端并执行滑动窗口检查
        redis_client = await get_redis()
        rate_key = RedisKeys.rate_limit_key(identifier, self.action)
        is_allowed = await self._is_allowed_sliding_window(
            redis_client,
            rate_key,
            self.times,
            self.seconds
        )
        if not is_allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"请求过于频繁，触发限流规则（单秒限流 {self.times} 次），请稍后再试",
                headers={"Retry-After": str(self.seconds)}
            )

    async def _is_allowed_sliding_window(
        self,
        redis_client: aioredis.Redis,
        rate_key: str,
        limit: int,
        window_seconds: int
    ) -> bool:
        """
        执行滑动窗口算法 (使用 Redis Pipeline 保证多次命令在一个 RTT 内完成)
        """
        now = time.time()
        clear_before = now - window_seconds

        # 使用 pipeline 批量操作提高执行效率
        async with redis_client.pipeline(transaction=True) as pipe:
            # 1. 移除当前窗口以前的过期记录
            pipe.zremrangebyscore(rate_key, 0, clear_before)
            # 2. 获取当前滑动窗口内的总请求次数
            pipe.zcard(rate_key)
            # 3. 将当前请求作为新元素加入 ZSET (score 和 member 均使用时间戳浮点数)
            # member 加入唯一纳秒避免同一毫秒覆盖
            member = f"{now}"
            pipe.zadd(rate_key, {member: now})
            # 4. 为 ZSET 键设置 TTL，防止冷数据长期滞留内存
            pipe.expire(rate_key, window_seconds * 2 + 1)

            results = await pipe.execute()

        # results[1] 是 ZCARD 返回的加入前的当前窗口元素个数
        current_requests_count = results[1]

        # 如果已有请求数已经达到或超过阈值，说明超额，且需移除刚刚插入的 member
        if current_requests_count >= limit:
            # 移除刚才误加入的请求
            await redis_client.zrem(rate_key, member)
            return False

        return True
