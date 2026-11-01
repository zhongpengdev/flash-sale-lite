"""
订单与商品服务层 (Order & Product Service)

【面试核心考点深度解析】：
1. 为什么需要库存预热 (Warm Up)？
   - 秒杀开始瞬间，流量呈断崖式脉冲涌入。如果第一波请求去访问数据库读取库存再载入 Redis，
     会引发经典的“缓存击穿 (Cache Breakdown)”问题，数万请求同时穿透到数据库，导致数据库当场崩溃。
   - 活动开始前，通过运营管理接口或定时脚本将商品物理库存提前预热进 Redis 内存，
     使秒杀开始后的读写流量全部命中 Redis 缓存，实现真正的“零数据库读查压力”。
2. 订单状态查询的高性能设计 (缓存穿透与兜底)：
   - 查询订单状态接口 (轮询) 先查 Redis 状态缓存；
   - 只有缓存未命中（例如过期）时，才降级兜底查询 PostgreSQL。
"""

import json
import logging
from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status
import redis.asyncio as aioredis
from app.core.redis import RedisKeys
from app.models.product import Product
from app.models.order import Order, OrderStatus
from app.schemas.product import ProductCreate, ProductWarmUpResponse

logger = logging.getLogger(__name__)

STATUS_DESC_MAP = {
    -1: "排队中",
    OrderStatus.PENDING.value: "待支付",
    OrderStatus.PAID.value: "已支付",
    OrderStatus.CANCELLED.value: "已取消",
}


class OrderService:
    """订单与商品业务服务"""

    @classmethod
    async def create_product(cls, db: AsyncSession, product_in: ProductCreate) -> Product:
        """创建商品"""
        product = Product(
            title=product_in.title,
            price=product_in.price,
            stock=product_in.stock,
            start_time=product_in.start_time,
            end_time=product_in.end_time,
            is_warmed_up=False,
        )
        db.add(product)
        await db.commit()
        await db.refresh(product)
        return product

    @classmethod
    async def get_product(cls, db: AsyncSession, product_id: int) -> Product:
        """根据 ID 获取商品信息"""
        result = await db.execute(select(Product).where(Product.id == product_id))
        product = result.scalar_one_or_none()
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"商品 ID {product_id} 不存在"
            )
        return product

    @classmethod
    async def list_products(cls, db: AsyncSession, skip: int = 0, limit: int = 20) -> List[Product]:
        """商品列表查询"""
        result = await db.execute(select(Product).offset(skip).limit(limit))
        return list(result.scalars().all())

    @classmethod
    async def warm_up_stock(
        cls,
        product_id: int,
        db: AsyncSession,
        redis_client: aioredis.Redis,
    ) -> ProductWarmUpResponse:
        """
        秒杀库存预热接口业务实现
        1. 从数据库读取商品最新物理库存
        2. 写入 Redis 缓存 key: flash_sale:stock:{product_id}
        3. 清空已购买用户集合 key: flash_sale:users:{product_id} (重置活动)
        4. 更新商品 is_warmed_up 状态并落库
        """
        product = await cls.get_product(db, product_id)

        stock_key = RedisKeys.stock_key(product_id)
        users_set_key = RedisKeys.users_set_key(product_id)

        # 写入 Redis
        await redis_client.set(stock_key, product.stock)
        # 清空已购用户集合（重置/预热）
        await redis_client.delete(users_set_key)

        # 标记数据库商品为已预热
        product.is_warmed_up = True
        await db.commit()
        await db.refresh(product)

        logger.info(f"商品 {product_id} 库存预热完成: stock={product.stock}")

        return ProductWarmUpResponse(
            product_id=product.id,
            title=product.title,
            stock_in_db=product.stock,
            stock_in_redis=product.stock,
            message="秒杀商品库存成功预热至 Redis 缓存！",
        )

    @classmethod
    async def get_order_status(
        cls,
        order_id: str,
        db: AsyncSession,
        redis_client: aioredis.Redis,
    ) -> dict:
        """
        高性能查询订单状态：
        先查 Redis 缓存；缓存未命中时查 PostgreSQL 数据库兜底。
        """
        # 1. 查询 Redis 缓存
        cache_key = RedisKeys.order_status_key(order_id)
        cached_data = await redis_client.get(cache_key)
        if cached_data:
            try:
                data = json.loads(cached_data)
                return {
                    "order_id": data.get("order_id", order_id),
                    "user_id": data.get("user_id"),
                    "product_id": data.get("product_id"),
                    "status": data.get("status", -1),
                    "status_desc": data.get("status_desc", STATUS_DESC_MAP.get(data.get("status"), "处理中")),
                    "created_at": None,
                }
            except Exception:
                pass

        # 2. 缓存未命中，降级查数据库
        result = await db.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"未找到订单 {order_id}"
            )

        status_desc = STATUS_DESC_MAP.get(order.status, "未知状态")
        return {
            "order_id": order.id,
            "user_id": order.user_id,
            "product_id": order.product_id,
            "status": order.status,
            "status_desc": status_desc,
            "created_at": order.created_at,
        }
