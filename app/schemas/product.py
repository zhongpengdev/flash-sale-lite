"""
商品相关的 Pydantic 校验模型 (Product Schemas)
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class ProductBase(BaseModel):
    title: str = Field(..., max_length=100, description="商品标题")
    price: Decimal = Field(..., gt=0, decimal_places=2, description="秒杀价格")
    stock: int = Field(..., ge=0, description="秒杀初始库存")
    start_time: datetime = Field(..., description="秒杀开始时间")
    end_time: datetime = Field(..., description="秒杀结束时间")


class ProductCreate(ProductBase):
    pass


class ProductResponse(ProductBase):
    id: int = Field(..., description="商品ID")
    is_warmed_up: bool = Field(..., description="是否已预热至缓存")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")

    model_config = ConfigDict(from_attributes=True)


class ProductWarmUpResponse(BaseModel):
    product_id: int = Field(..., description="商品ID")
    title: str = Field(..., description="商品标题")
    stock_in_db: int = Field(..., description="数据库库存")
    stock_in_redis: int = Field(..., description="写入Redis缓存的库存")
    message: str = Field(..., description="预热结果说明")
