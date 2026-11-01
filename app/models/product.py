"""
秒杀商品 ORM 模型 (Product Model)
"""

from datetime import datetime
from decimal import Decimal
from sqlalchemy import Integer, String, Numeric, Boolean, DateTime, func, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models import Base


class Product(Base):
    """
    秒杀商品实体表
    【设计要点】：
    1. stock 字段添加 CHECK (stock >= 0) 数据库约束，作为数据库物理防线的兜底，杜绝负库存。
    2. is_warmed_up 标识是否已完成 Redis 缓存预热。
    """
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    title: Mapped[str] = mapped_column(String(100), nullable=False, comment="商品标题/名称")
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, comment="秒杀价格")
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="数据库物理库存")
    is_warmed_up: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="是否已预热至Redis")
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="秒杀开始时间")
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="秒杀结束时间")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="更新时间"
    )

    # 关联订单
    orders = relationship("Order", back_populates="product")

    __table_args__ = (
        CheckConstraint("stock >= 0", name="check_positive_stock"),
    )

    def __repr__(self) -> str:
        return f"<Product id={self.id} title='{self.title}' stock={self.stock}>"
