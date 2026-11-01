"""
高并发秒杀防超卖压测与验证脚本 (Benchmark & Anti-Overselling Verification Script)

【面试展示神器】：
向面试官直观证明：
1. 100 个并发协程并发抢购只有 10 件库存的商品；
2. 命中 10 次成功 (HTTP 200)，其余 90 次精准因“已售罄”被拒绝 (HTTP 400)；
3. Worker 异步消费完成后，对比 Redis 剩余库存与 PostgreSQL 订单表、商品物理库存；
4. 验证零超卖（Zero Overselling）、数据最终一致性。

【运行方式】：
python scripts/benchmark.py
"""

import sys
import os
import time
import asyncio
from typing import List, Dict, Any
import httpx
from sqlalchemy import select, func
import redis.asyncio as aioredis

# 将项目根目录加入模块搜索路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.redis import RedisKeys
from app.models import Product, Order

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
BUY_URL = f"{API_BASE_URL}/api/v1/flash-sale/buy"
WARMUP_URL = f"{API_BASE_URL}/api/v1/products/1/warm-up"
PRODUCT_ID = 1
TOTAL_CONCURRENT_USERS = 100


async def send_buy_request(client: httpx.AsyncClient, user_id: int) -> Dict[str, Any]:
    """发起单个秒杀请求"""
    payload = {
        "product_id": PRODUCT_ID,
        "user_id": user_id,
    }
    headers = {
        "X-User-Id": str(user_id),
        "Content-Type": "application/json",
    }
    try:
        response = await client.post(BUY_URL, json=payload, headers=headers, timeout=10.0)
        return {
            "user_id": user_id,
            "status_code": response.status_code,
            "json": response.json() if response.headers.get("content-type", "").startswith("application/json") else {},
            "raw": response.text,
        }
    except Exception as e:
        return {
            "user_id": user_id,
            "status_code": 0,
            "error": str(e),
        }


async def run_benchmark():
    print("=" * 70)
    print("      🚀 Flash Sale Lite 高并发秒杀防超卖压测验证开始 🚀")
    print("=" * 70)
    print(f"目标接口: {BUY_URL}")
    print(f"并发请求数: {TOTAL_CONCURRENT_USERS} 个协程")
    print(f"测试商品 ID: {PRODUCT_ID} (初始库存: 10 件)\n")

    # 1. 检查后端主服务是否在线
    async with httpx.AsyncClient() as client:
        try:
            health = await client.get(f"{API_BASE_URL}/health", timeout=3.0)
            if health.status_code != 200:
                print(f"❌ 后端服务健康检查失败 (状态码 {health.status_code})，请先启动 FastAPI 服务！")
                return
        except Exception as e:
            print(f"❌ 无法连接到后端服务 ({API_BASE_URL}): {e}")
            print("👉 请确保已在终端执行: uvicorn app.main:app --host 0.0.0.0 --port 8000")
            return

    # 2. 自动重置数据库环境并调用库存预热接口
    print("[阶段 1] 正在重置测试环境并调用接口对商品进行库存预热...")
    from sqlalchemy import text
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE TABLE orders;"))
        await session.execute(text("UPDATE products SET stock = 10, is_warmed_up = false WHERE id = :pid;"), {"pid": PRODUCT_ID})
        await session.commit()

    async with httpx.AsyncClient() as client:
        warmup_resp = await client.post(WARMUP_URL)
        if warmup_resp.status_code == 200:
            print("   ✓ 库存预热成功: PostgreSQL 物理库存已置为 10，Redis 缓存库存已同步置为 10，去重集合已重置。")
        else:
            print(f"   ⚠️ 预热请求返回: {warmup_resp.status_code} - {warmup_resp.text}")

    # 3. 发起 100 个并发秒杀请求
    print(f"\n[阶段 2] 正在瞬间发射 {TOTAL_CONCURRENT_USERS} 个并发秒杀请求 (asyncio + httpx)...")
    start_time = time.time()

    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=200, max_keepalive_connections=50)) as client:
        tasks = [send_buy_request(client, user_id) for user_id in range(1, TOTAL_CONCURRENT_USERS + 1)]
        results = await asyncio.gather(*tasks)

    elapsed = time.time() - start_time
    qps = TOTAL_CONCURRENT_USERS / elapsed if elapsed > 0 else 0
    print(f"   ✓ 并发请求全部完成！耗时: {elapsed:.3f} 秒 (估算 QPS: {qps:.1f} req/s)")

    # 4. 统计 HTTP 响应分布
    success_count = 0
    sold_out_count = 0
    duplicate_count = 0
    rate_limited_count = 0
    other_error_count = 0
    order_ids = []

    for r in results:
        code = r.get("status_code")
        body = r.get("json", {})
        detail = body.get("detail", "")
        message = body.get("message", "")

        if code == 200:
            success_count += 1
            if "order_id" in body and body["order_id"]:
                order_ids.append(body["order_id"])
        elif code == 400:
            if "售罄" in detail or "不足" in detail:
                sold_out_count += 1
            elif "已参与" in detail or "重复" in detail:
                duplicate_count += 1
            else:
                sold_out_count += 1
        elif code == 429:
            rate_limited_count += 1
        else:
            other_error_count += 1

    print("\n[阶段 3] HTTP 响应分布统计:")
    print(f"   ├─ 抢购成功数 (HTTP 200):        {success_count:>3} 次  {'✓ (精准等于库存 10)' if success_count == 10 else '❌ (异常)'}")
    print(f"   ├─ 提示已售罄数 (HTTP 400):      {sold_out_count:>3} 次  {'✓ (精准拦截 90 人)' if sold_out_count == 90 else '❌'}")
    print(f"   ├─ 重复购买拦截 (HTTP 400):      {duplicate_count:>3} 次")
    print(f"   ├─ 触发限流拦截 (HTTP 429):      {rate_limited_count:>3} 次")
    print(f"   └─ 其他错误响应:                 {other_error_count:>3} 次")

    # 5. 等待 Celery Worker 异步落库
    print("\n[阶段 4] 等待 3 秒让 Celery Worker 异步消费队列并完成落库...")
    for sec in range(3, 0, -1):
        print(f"   ⏳ 剩余等待时间: {sec} 秒...", end="\r")
        await asyncio.sleep(1)
    print("   ✓ 等待结束，开始验证底层数据库与缓存的一致性！          ")

    # 6. 查询 Redis 与 PostgreSQL 数据进行核验
    print("\n[阶段 5] 正在校验 Redis 缓存与 PostgreSQL 数据库数据...")
    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        redis_stock_val = await redis_client.get(RedisKeys.stock_key(PRODUCT_ID))
        redis_stock = int(redis_stock_val) if redis_stock_val is not None else -999
        redis_users_count = await redis_client.scard(RedisKeys.users_set_key(PRODUCT_ID))
    finally:
        await redis_client.aclose()

    async with AsyncSessionLocal() as session:
        # 查询数据库 orders 订单总数
        db_orders_res = await session.execute(
            select(func.count()).select_from(Order).where(Order.product_id == PRODUCT_ID)
        )
        db_orders_count = db_orders_res.scalar() or 0

        # 查询数据库 products 实际剩余库存
        db_product_res = await session.execute(
            select(Product).where(Product.id == PRODUCT_ID)
        )
        db_product = db_product_res.scalar_one_or_none()
        db_stock = db_product.stock if db_product else -999

    print("┌────────────────────────────────────────────────────────┐")
    print("│                   最终数据核验对照表                   │")
    print("├──────────────────────┬──────────┬──────────┬───────────┤")
    print("│ 核验指标             │ 预期数值 │ 实际数值 │ 判定结果  │")
    print("├──────────────────────┼──────────┼──────────┼───────────┤")
    print(f"│ 接口抢购成功总数     │    10    │   {success_count:>4}   │  {'PASS ✓' if success_count == 10 else 'FAIL ✗'}   │")
    print(f"│ Redis 剩余库存       │     0    │   {redis_stock:>4}   │  {'PASS ✓' if redis_stock == 0 else 'FAIL ✗'}   │")
    print(f"│ Redis 购买用户数     │    10    │   {redis_users_count:>4}   │  {'PASS ✓' if redis_users_count == 10 else 'FAIL ✗'}   │")
    print(f"│ PostgreSQL 订单总数  │    10    │   {db_orders_count:>4}   │  {'PASS ✓' if db_orders_count == 10 else 'FAIL ✗'}   │")
    print(f"│ PostgreSQL 剩余库存  │     0    │   {db_stock:>4}   │  {'PASS ✓' if db_stock == 0 else 'FAIL ✗'}   │")
    print("└──────────────────────┴──────────┴──────────┴───────────┘")

    # 综合判定
    all_passed = (
        success_count == 10
        and sold_out_count == 90
        and redis_stock == 0
        and redis_users_count == 10
        and db_orders_count == 10
        and db_stock == 0
    )

    print("\n" + "=" * 70)
    if all_passed:
        print(" 🎉【验证结论】：防超卖高并发验证全部通过 (ALL PASSED)！")
        print("   - 10 件库存精准售罄，无少卖、无多卖、无超卖！")
        print("   - Redis Lua 内存扣减与 PostgreSQL 异步落库严格保证最终一致性！")
    else:
        print(" ⚠️【验证结论】：数据指标存在偏差，请检查 Celery Worker 是否正在运行！")
        if db_orders_count < success_count:
            print(f"   提示：数据库订单数为 {db_orders_count}，少于成功数 {success_count}。请检查 Celery Worker 是否启动消费！")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_benchmark())
