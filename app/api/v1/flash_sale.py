"""
秒杀触发与订单状态查询路由 (Flash Sale API)

【面试核心考点深度解析】：
1. 为什么抢购接口禁止直连数据库？
   - 任何涉及直接向 MySQL/PostgreSQL 发送 SELECT 或 UPDATE 的逻辑都会在并发峰值时耗尽数据库连接池。
   - 此处严格遵守高并发设计原则：将高频读写全部下沉至 Redis 内存级，数据库仅由 Celery Worker 异步低频消费。
2. 接口分流与状态机：
   - 客户端调用 POST /api/v1/flash-sale/buy 拿到 order_id 后，前端应启动定时器轮询 GET /api/v1/flash-sale/order/{order_id}，
     当状态从“排队中 (-1)”跃迁到“待支付 (0)”时，跳转收银台引导用户付款。
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from app.core.database import get_db
from app.core.redis import get_redis
from app.middlewares.rate_limiter import RateLimiter
from app.schemas.order import (
    FlashSaleBuyRequest,
    FlashSaleBuyResponse,
    OrderStatusResponse,
)
from app.services.flash_sale import FlashSaleService
from app.services.order import OrderService

router = APIRouter(prefix="/flash-sale", tags=["秒杀与订单"])


@router.post(
    "/buy",
    response_model=FlashSaleBuyResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RateLimiter(times=5, seconds=1, action="flash_sale_buy"))],
    summary="秒杀抢购下单 (核心防超卖接口)",
)
async def flash_sale_buy(
    request_data: FlashSaleBuyRequest,
    redis_client: aioredis.Redis = Depends(get_redis),
):
    """
    【高并发秒杀核心接口】
    - 前置挂载 RateLimiter 依赖：单 IP / 单用户 每秒限流 5 次 (防连点器/爬虫)
    - 内存原子扣减：执行 Redis Lua 脚本，原子完成「单人去重 + 库存校验 + 内存扣减」
    - 异步削峰：抢购成功后向 Celery 发送异步落库任务，接口立即返回 HTTP 200 与 order_id
    - 快速失败：已售罄返回 400，重复抢购返回 400
    """
    code, message, order_id = await FlashSaleService.buy(
        product_id=request_data.product_id,
        user_id=request_data.user_id,
        redis_client=redis_client,
    )

    if code == 1:
        # 抢购成功，进入 Celery 异步落库队列
        return FlashSaleBuyResponse(
            code=200,
            message=message,
            order_id=order_id,
        )
    elif code == 0:
        # 库存不足 (售罄)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        )
    elif code == -1:
        # 单人限购，重复购买
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="系统繁忙，请稍后重试",
        )


@router.get(
    "/order/{order_id}",
    response_model=OrderStatusResponse,
    summary="查询秒杀订单排队与支付状态",
)
async def get_order_status(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    """
    【订单状态轮询接口】
    - 优先从 Redis 缓存获取订单状态 (极速响应，无需打扰数据库)
    - Redis 缓存失效时降级查询 PostgreSQL 数据库
    """
    order_info = await OrderService.get_order_status(order_id, db, redis_client)
    return OrderStatusResponse(**order_info)
