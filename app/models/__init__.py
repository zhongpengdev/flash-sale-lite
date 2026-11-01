"""
SQLAlchemy ORM 模型基类与模型导出
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# 统一导出所有 ORM 模型，方便 Alembic 及 Base.metadata 创建所有表
from app.models.user import User
from app.models.product import Product
from app.models.order import Order, OrderStatus

__all__ = ["Base", "User", "Product", "Order", "OrderStatus"]
