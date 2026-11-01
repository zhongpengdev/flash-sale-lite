"""
Celery 异步任务应用实例配置 (Celery App Configuration)

【面试核心考点】：
1. 为什么要使用 Celery 做异步削峰？
   - 秒杀瞬间产生巨大并发，如果让 FastAPI 直接同步写 PostgreSQL，数据库连接池瞬间被打满，
     单行商品库存行锁竞争剧烈，接口响应时间飙升（从数毫秒恶化到数秒甚至超时崩溃）。
   - 将“库存已扣减成功”的订单以消息形式投递至 Celery 队列，FastAPI 立即返回“排队中”，
     而后台 Celery Worker 以恒定、受控的速率消费队列、批量落库，实现平滑削峰。
2. Broker 与 Result Backend 选型：
   - 使用 Redis 作为轻量级 Broker 与 Backend，运维部署成本极低（单个 Redis 服务切分 DB 即可），
     完全满足轻量级高并发系统的吞吐需求。
"""

from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "flash_sale_worker",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.worker.tasks"],
)

# Celery 基础参数优化
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    # 任务预取数量：设置为 1，防止某个 worker 进程一次性囤积过多任务导致处理不均
    worker_prefetch_multiplier=1,
    # 任务失败重试配置
    task_acks_late=True,
)
