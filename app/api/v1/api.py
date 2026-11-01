"""
API v1 路由汇聚中心
"""

from fastapi import APIRouter
from app.api.v1.products import router as products_router
from app.api.v1.flash_sale import router as flash_sale_router

api_router = APIRouter()
api_router.include_router(products_router)
api_router.include_router(flash_sale_router)
