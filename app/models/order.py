"""
订单 ORM 模型与状态枚举 (Order Model & Status)
"""

import enum
import uuid
from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models import Base


class OrderStatus(enum.IntEnum):
    """
    订单生命周期状态枚举
    """
    PENDING = 0     # 待支付 (等待用户支付)
    PAID = 1        # 已支付 (支付成功履约)
    CANCELLED = 2   # 已取消 (用户主动取消或超时未支付回滚)


class Order(Base):
    """
    秒杀订单实体表
    【设计要点】：
    1. id 采用 36 位 UUID 字符串，在分布式秒杀场景下比自增 ID 具备更高安全性和防暴力爬取特性。
    2. 针对 user_id, product_id, status 建立联合或独立索引，支撑高频查询。
    """
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="订单全局唯一UUID"
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="购买用户ID"
    )
    product_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="秒杀商品ID"
    )
    status: Mapped[int] = mapped_column(
        Integer,
        default=OrderStatus.PENDING.value,
        nullable=False,
        index=True,
        comment="订单状态(0:待支付, 1:已支付, 2:已取消)"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="订单创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="更新时间"
    )

    # 关联模型
    user = relationship("User", back_populates="orders")
    product = relationship("Product", back_populates="orders")

    def __repr__(self) -> str:
        return f"<Order id='{self.id}' user_id={self.user_id} product_id={self.product_id} status={self.status}>"
