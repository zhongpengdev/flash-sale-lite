"""
订单相关的 Pydantic 校验模型 (Order Schemas)
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class FlashSaleBuyRequest(BaseModel):
    """
    秒杀下单请求入参
    """
    product_id: int = Field(..., gt=0, description="秒杀商品ID")
    user_id: int = Field(..., gt=0, description="抢购用户ID")


class FlashSaleBuyResponse(BaseModel):
    """
    秒杀下单统一响应结构
    """
    code: int = Field(..., description="业务状态码: 200 成功排队, 400 业务拦截")
    message: str = Field(..., description="状态提示文案")
    order_id: Optional[str] = Field(None, description="排队生成的订单UUID")


class OrderStatusResponse(BaseModel):
    """
    订单轮询状态响应
    """
    order_id: str = Field(..., description="订单UUID")
    user_id: Optional[int] = Field(None, description="用户ID")
    product_id: Optional[int] = Field(None, description="商品ID")
    status: int = Field(..., description="订单状态(0:待支付, 1:已支付, 2:已取消, -1:队列中)")
    status_desc: str = Field(..., description="状态可读描述")
    created_at: Optional[datetime] = Field(None, description="订单创建时间")


class OrderResponse(BaseModel):
    """
    完整订单响应
    """
    id: str
    user_id: int
    product_id: int
    status: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
