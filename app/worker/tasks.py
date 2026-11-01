"""
Celery 异步任务定义 (Async Tasks)

【面试核心考点深度解析】：
1. 消息幂等性 (Idempotency)：
   - 消息中间件可能因网络超时、Worker 重试等原因导致同一条消息被投递多次（At-Least-Once 语义）。
   - 我们通过订单主键 UUID (order_id) 进行防重：入库前先查该 order_id 是否已存在，存在则直接跳过，杜绝重复插入。
2. 异步削峰落库 (create_order_task)：
   - 在独立数据库事务中，写入 orders 表并对 products 表执行原子扣减 (UPDATE ... WHERE id = :id AND stock > 0)。
   - 提交成功后，立即调度一个 15 分钟后的延时任务 (cancel_unpaid_order_task)。
3. 超时未支付取消与库存回滚 (cancel_unpaid_order_task)：
   - 15分钟倒计时到期时，Worker 消费该任务，检查订单是否仍为 PENDING。
   - 若未支付：将订单改为 CANCELLED，在 DB 中恢复物理库存，并通过 Redis INCR 恢复缓存库存，SREM 移除用户抢购记录。
   - 保证了“缓存与数据库”的最终一致性，并允许该用户重新参与秒杀。
"""

import json
import logging
from sqlalchemy import update
from app.worker.celery_app import celery_app
from app.core.config import settings
from app.core.database import get_sync_db
from app.core.redis import get_sync_redis, RedisKeys
from app.models.order import Order, OrderStatus
from app.models.product import Product

logger = logging.getLogger(__name__)


@celery_app.task(name="create_order_task", bind=True, max_retries=3, default_retry_delay=2)
def create_order_task(self, order_id: str, user_id: int, product_id: int) -> dict:
    """
    异步落库任务：消费秒杀成功的消息，生成订单并扣减数据库物理库存
    """
    logger.info(f"开始处理订单创建任务: order_id={order_id}, user_id={user_id}, product_id={product_id}")

    with get_sync_db() as db:
        try:
            # 1. 幂等性校验：防止因 Celery 任务重试导致生成重复订单
            existing_order = db.query(Order).filter(Order.id == order_id).first()
            if existing_order:
                logger.warning(f"订单 {order_id} 已存在，跳过重复创建（保证幂等性）")
                return {"status": "skipped", "order_id": order_id, "message": "Order already exists"}

            # 2. 插入待支付订单
            new_order = Order(
                id=order_id,
                user_id=user_id,
                product_id=product_id,
                status=OrderStatus.PENDING.value,
            )
            db.add(new_order)

            # 3. 扣减数据库实际物理库存 (使用条件更新防止超卖兜底)
            # UPDATE products SET stock = stock - 1 WHERE id = :product_id AND stock > 0
            stmt = (
                update(Product)
                .where(Product.id == product_id, Product.stock > 0)
                .values(stock=Product.stock - 1)
            )
            result = db.execute(stmt)

            if result.rowcount == 0:
                # 理论上 Redis Lua 已经拦截超卖，若此处行数为 0，说明数据库与缓存数据严重不一致
                logger.critical(f"数据库库存扣减失败！商品 {product_id} 数据库库存不足，准备回滚！")
                db.rollback()
                # 回滚 Redis 缓存
                sync_redis = get_sync_redis()
                sync_redis.incr(RedisKeys.stock_key(product_id))
                sync_redis.srem(RedisKeys.users_set_key(product_id), str(user_id))
                return {"status": "error", "order_id": order_id, "message": "Database stock underflow"}

            # 提交事务
            db.commit()
            logger.info(f"订单 {order_id} 异步落库成功，数据库物理库存已同步扣减 1")

            # 4. 更新 Redis 中的订单状态缓存 (方便前端/客户端高效轮询，无需打扰数据库)
            sync_redis = get_sync_redis()
            order_cache_data = {
                "order_id": order_id,
                "user_id": user_id,
                "product_id": product_id,
                "status": OrderStatus.PENDING.value,
                "status_desc": "待支付",
            }
            sync_redis.set(
                RedisKeys.order_status_key(order_id),
                json.dumps(order_cache_data),
                ex=settings.ORDER_EXPIRE_SECONDS + 300,
            )

            # 5. 触发延时任务：15 分钟后检查是否未支付并自动取消回滚
            cancel_unpaid_order_task.apply_async(
                args=[order_id],
                countdown=settings.ORDER_EXPIRE_SECONDS,
            )
            logger.info(f"已为订单 {order_id} 设置 {settings.ORDER_EXPIRE_SECONDS} 秒后的超时取消延时任务")

            return {"status": "success", "order_id": order_id}

        except Exception as exc:
            db.rollback()
            logger.error(f"处理创建订单任务发生异常: {exc}，触发重试...")
            # 重试机制：网络抖动或瞬时死锁时由 Celery 自动重试
            raise self.retry(exc=exc)


@celery_app.task(name="cancel_unpaid_order_task", bind=True)
def cancel_unpaid_order_task(self, order_id: str) -> dict:
    """
    超时未支付订单自动取消与库存回滚任务
    """
    logger.info(f"触发超时检查任务: order_id={order_id}")

    with get_sync_db() as db:
        try:
            # 1. 查询订单当前状态
            order = db.query(Order).filter(Order.id == order_id).first()
            if not order:
                logger.warning(f"超时取消任务未找到对应订单: {order_id}")
                return {"status": "not_found", "order_id": order_id}

            # 2. 只有当订单状态仍为 PENDING (待支付) 时才执行取消与库存回滚
            if order.status == OrderStatus.PENDING.value:
                logger.info(f"订单 {order_id} 超时未支付，执行取消流转与库存回滚")
                order.status = OrderStatus.CANCELLED.value

                # 恢复数据库中对应商品的物理库存
                stmt = (
                    update(Product)
                    .where(Product.id == order.product_id)
                    .values(stock=Product.stock + 1)
                )
                db.execute(stmt)
                db.commit()

                # 3. 回滚 Redis 缓存：库存 +1，并将该用户从秒杀去重集合中移除
                sync_redis = get_sync_redis()
                sync_redis.incr(RedisKeys.stock_key(order.product_id))
                sync_redis.srem(RedisKeys.users_set_key(order.product_id), str(order.user_id))

                # 4. 更新订单状态缓存
                order_cache_data = {
                    "order_id": order_id,
                    "user_id": order.user_id,
                    "product_id": order.product_id,
                    "status": OrderStatus.CANCELLED.value,
                    "status_desc": "已超时自动取消",
                }
                sync_redis.set(
                    RedisKeys.order_status_key(order_id),
                    json.dumps(order_cache_data),
                    ex=3600,
                )
                logger.info(f"订单 {order_id} 已取消，Redis 与数据库库存均已成功回滚")
                return {"status": "cancelled", "order_id": order_id}
            else:
                logger.info(f"订单 {order_id} 当前状态为 {order.status} (非待支付)，无需超时取消")
                return {"status": "ignored", "order_id": order_id, "current_status": order.status}

        except Exception as exc:
            db.rollback()
            logger.error(f"执行超时取消任务异常: {exc}")
            raise self.retry(exc=exc, max_retries=3, default_retry_delay=5)
