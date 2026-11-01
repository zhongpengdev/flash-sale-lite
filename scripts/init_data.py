"""
数据初始化与秒杀活动预热脚本 (Data Initialization & Warm-up Script)

【功能说明】：
1. 重置或初始化数据库表结构 (Base.metadata.create_all)。
2. 创建 100 位测试用户 (user_1 ~ user_100)。
3. 创建 1 个测试秒杀商品 (库存精准设置为 10 件)。
4. 预热商品库存至 Redis (flash_sale:stock:1 = 10)，并清空已购集合与历史订单缓存。

【使用方式】：
python scripts/init_data.py
"""

import sys
import os
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

# 将项目根目录加入模块搜索路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select, delete
import redis.asyncio as aioredis

from app.core.config import settings
from app.core.database import async_engine, AsyncSessionLocal
from app.core.redis import RedisKeys
from app.models import Base, User, Product, Order


async def init_data():
    print("=" * 60)
    print(" [Flash Sale Lite] 正在开始初始化系统数据与缓存预热...")
    print("=" * 60)

    # 1. 确保数据库表已建好
    print("\n1. 正在同步数据库表结构...")
    async with async_engine.begin() as conn:
        # 重建或者确保表存在
        await conn.run_sync(Base.metadata.create_all)
    print("   ✓ PostgreSQL 数据表同步完成！")

    # 2. 初始化测试用户与商品
    async with AsyncSessionLocal() as session:
        print("\n2. 正在清理历史测试数据并生成 100 位测试用户...")
        from sqlalchemy import text
        # 清理旧数据并重置序列，保证每次测试环境纯净且 ID 从 1 开始
        await session.execute(text("TRUNCATE TABLE orders, products, users RESTART IDENTITY CASCADE;"))
        await session.commit()

        # 生成 100 位独立用户 (明确指定 ID 1 ~ 100)
        users = [
            User(
                id=i,
                username=f"tester_{i}",
                email=f"tester_{i}@example.com"
            )
            for i in range(1, 101)
        ]
        session.add_all(users)
        await session.commit()
        print(f"   ✓ 成功创建 100 位测试用户 (ID 1 ~ 100)")

        # 创建秒杀商品 (库存 10 件)
        print("\n3. 正在创建秒杀测试商品 (库存: 10 件)...")
        now = datetime.now()
        product = Product(
            id=1,
            title="iPhone 15 Pro Max 256GB 原色钛金属",
            price=Decimal("99.00"),
            stock=10,  # 仅 10 件库存
            is_warmed_up=True,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(days=1),
        )
        session.add(product)
        await session.commit()
        await session.refresh(product)
        print(f"   ✓ 成功创建测试商品: ID={product.id}, 标题={product.title}, 物理库存={product.stock}")

    # 3. 预热 Redis 缓存
    print("\n4. 正在连接 Redis 并执行库存预热与缓存重置...")
    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        stock_key = RedisKeys.stock_key(1)
        users_set_key = RedisKeys.users_set_key(1)

        # 写入库存
        await redis_client.set(stock_key, 10)
        # 清空已购集合
        await redis_client.delete(users_set_key)

        # 清除限流与历史订单状态键
        keys_to_clean = []
        async for key in redis_client.scan_iter("rate_limit:*"):
            keys_to_clean.append(key)
        async for key in redis_client.scan_iter("flash_sale:order_status:*"):
            keys_to_clean.append(key)
        if keys_to_clean:
            await redis_client.delete(*keys_to_clean)

        current_stock = await redis_client.get(stock_key)
        print(f"   ✓ Redis 缓存预热成功！")
        print(f"     - 缓存键 [{stock_key}] 当前数值: {current_stock}")
        print(f"     - 用户去重键 [{users_set_key}] 已初始化重置")
    finally:
        await redis_client.aclose()

    print("\n" + "=" * 60)
    print(" [初始化就绪] 系统测试数据已准备完毕，可启动服务并运行压测脚本！")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(init_data())
