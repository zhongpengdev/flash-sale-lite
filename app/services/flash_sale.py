"""
核心秒杀业务服务层 (Flash Sale Service)
实现 Redis + Lua 原子防超卖与单人限购校验，并异步投递 Celery 任务。

【面试高频核心考点深度解析】：
1. 为什么不用 MySQL 行锁 (SELECT FOR UPDATE)？
   - 在高并发秒杀场景下，数万并发若直接打到 MySQL 执行 SELECT ... FOR UPDATE，
     所有事务都会争抢同一行商品记录的互斥锁 (X Lock)。
   - 行锁竞争会导致大量事务排队等待、锁超时、数据库 CPU 100%、连接池耗尽，最终击垮数据库造成雪崩。
2. 为什么不用 Redis 单独的 DECR 命令？
   - 简单的 DECR 只能做到扣减，无法在一个原子操作内同时完成【单人防重校验】+【库存是否充足检查】+【扣库存】+【记录用户】。
   - 如果分步执行（先 SISMEMBER 再 GET 再 DECR），由于网络延迟和并发穿插，必然产生竞态条件（Race Condition），导致超卖。
3. 为什么 Redis Lua 脚本能杜绝超卖？
   - Redis 采用单线程事件循环执行命令。
   - 执行 Lua 脚本期间，Redis 会将整个脚本作为一个原子操作执行，期间不会插入任何其他客户端的命令。
   - 因此“查已购 -> 查库存 -> 扣减 -> 记已购”具有绝对原子性，在内存级别（微秒级）完成，彻底消除了超卖和重复下单。
"""

import uuid
import json
import logging
from typing import Tuple, Optional
import redis.asyncio as aioredis
from app.core.redis import RedisKeys
from app.worker.tasks import create_order_task

logger = logging.getLogger(__name__)

# ==============================================================================
# 原子秒杀核心 Lua 脚本
# KEYS[1]: stock_key (商品库存键)
# KEYS[2]: users_set_key (已抢购成功用户集合键)
# ARGV[1]: user_id (抢购用户ID)
# 返回值约定：
#   1: 抢购成功，库存已扣减，用户已记录
#   0: 库存不足，秒杀售罄
#  -1: 该用户已经抢购过，禁止重复参与
# ==============================================================================
LUA_FLASH_SALE_SCRIPT = """
-- 1. 检查用户是否已经抢购成功过 (单人限购 1 件)
if redis.call('SISMEMBER', KEYS[2], ARGV[1]) == 1 then
    return -1
end

-- 2. 检查剩余库存
local stock = tonumber(redis.call('GET', KEYS[1]) or 0)
if stock <= 0 then
    return 0
end

-- 3. 原子扣除库存并记录用户至已购集合
redis.call('DECR', KEYS[1])
redis.call('SADD', KEYS[2], ARGV[1])
return 1
"""


class FlashSaleService:
    """秒杀领域业务服务"""

    @classmethod
    async def buy(
        cls,
        product_id: int,
        user_id: int,
        redis_client: aioredis.Redis,
    ) -> Tuple[int, str, Optional[str]]:
        """
        执行秒杀原子抢购流程
        :param product_id: 秒杀商品ID
        :param user_id: 抢购用户ID
        :param redis_client: 异步 Redis 客户端
        :return: (result_code, message, order_id)
                 result_code: 1 成功, 0 售罄, -1 重复购买
        """
        stock_key = RedisKeys.stock_key(product_id)
        users_set_key = RedisKeys.users_set_key(product_id)

        # 1. 内存原子操作：调用 Redis Lua 脚本执行原子扣减与去重
        result = await redis_client.eval(
            LUA_FLASH_SALE_SCRIPT,
            2,
            stock_key,
            users_set_key,
            str(user_id),
        )

        # 2. 根据 Lua 执行结果进行差异化分支处理
        if result == 1:
            # 【抢购成功】
            # 生成全局唯一的订单 UUID
            order_id = str(uuid.uuid4())

            # 提前在 Redis 中写入一个 "排队中" 状态，让前端/轮询接口立即可感知
            initial_status = {
                "order_id": order_id,
                "user_id": user_id,
                "product_id": product_id,
                "status": -1,  # -1 代表进入排队异步落库队列
                "status_desc": "排队中",
            }
            await redis_client.set(
                RedisKeys.order_status_key(order_id),
                json.dumps(initial_status),
                ex=600,
            )

            # 向 Celery 消息队列推送异步落库任务 (异步削峰，保护 MySQL)
            create_order_task.delay(order_id, user_id, product_id)
            logger.info(f"秒杀抢购成功，已派发落库任务: order_id={order_id}, user_id={user_id}")

            return 1, "抢购成功，订单排队创建中", order_id

        elif result == 0:
            # 【库存不足】
            logger.info(f"秒杀已售罄: product_id={product_id}, user_id={user_id}")
            return 0, "手慢了，商品已售罄", None

        elif result == -1:
            # 【重复购买】
            logger.warning(f"用户重复抢购被拦截: product_id={product_id}, user_id={user_id}")
            return -1, "您已参与过该商品秒杀", None

        else:
            logger.error(f"Lua 脚本执行异常，未预期返回值: {result}")
            return 0, "秒杀异常，请稍后重试", None
