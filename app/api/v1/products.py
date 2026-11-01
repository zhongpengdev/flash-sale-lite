"""
秒杀商品管理与库存预热路由 (Product API)
"""

from typing import List
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from app.core.database import get_db
from app.core.redis import get_redis
from app.schemas.product import (
    ProductCreate,
    ProductResponse,
    ProductWarmUpResponse,
)
from app.services.order import OrderService

router = APIRouter(prefix="/products", tags=["商品管理与预热"])


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建秒杀商品",
)
async def create_product(
    product_in: ProductCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    创建秒杀商品
    - 录入商品标题、价格、初始库存和活动起止时间
    """
    return await OrderService.create_product(db, product_in)


@router.get(
    "",
    response_model=List[ProductResponse],
    summary="查询秒杀商品列表",
)
async def list_products(
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """
    分页查询秒杀商品列表
    """
    return await OrderService.list_products(db, skip, limit)


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
    summary="获取单个秒杀商品详情",
)
async def get_product(
    product_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    根据商品 ID 查询商品详细信息
    """
    return await OrderService.get_product(db, product_id)


@router.post(
    "/{product_id}/warm-up",
    response_model=ProductWarmUpResponse,
    summary="秒杀库存预热 (核心接口)",
)
async def warm_up_product_stock(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    """
    【核心接口】秒杀商品库存预热
    - 读取 PostgreSQL 中的物理库存
    - 写入 Redis 缓存键 `flash_sale:stock:{product_id}`
    - 清空已购买用户去重集合 `flash_sale:users:{product_id}`
    - 保证秒杀开始时流量全部直接击中内存缓存，避免数据库穿透
    """
    return await OrderService.warm_up_stock(product_id, db, redis_client)
