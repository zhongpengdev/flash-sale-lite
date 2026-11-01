# Flash Sale Lite ⚡️

[![Python Version](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15%2B-336791.svg)](https://www.postgresql.org/)
[![Redis](https://img.shields.io/badge/Redis-7%2B-DC382D.svg)](https://redis.io/)
[![Celery](https://img.shields.io/badge/Celery-5.3%2B-37814A.svg)](https://docs.celeryq.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

一个基于 **FastAPI + Redis + Celery + PostgreSQL** 构建的**轻量级、工程规范、开箱即用**的高并发秒杀与订单履约系统。

本项目旨在用清晰优雅的单体分层架构（Clean Architecture），系统性攻克高并发场景下的四大经典难题：**原子防超卖（Redis+Lua）**、**异步削峰（Celery）**、**接口限流（滑动窗口）** 与 **超时未支付延时回滚（最终一致性）**。

> 💡 **面试与深入学习指南**：本项目包含完整的架构推演、选型考量与高频面试题标准口述，详见 👉 [【面试考点与架构精解 (learn/)】](learn/README.md)。

---

## 🏗 系统架构图 (Architecture)

系统严格采用分层设计，将高频读写全部下沉至 Redis 内存级，持久层数据库仅由后台 Worker 异步消费，实现动静分离与流量整形。

```
┌─────────────────────────────────────────────────────────────┐
│                      Client / Benchmark                     │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTP 并发请求
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                       FastAPI Web Layer                     │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ RateLimiter 中间件 (Redis ZSET 滑动窗口限流: 5次/秒)     │  │
│  └───────────────────────────┬───────────────────────────┘  │
│                              ▼                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ FlashSaleService (Redis Lua 原子脚本执行)              │  │
│  │ 1. SISMEMBER 检查单人限购                                │  │
│  │ 2. GET 检查库存是否 > 0                                  │  │
│  │ 3. DECR 扣减库存 & SADD 记录购买用户                      │  │
│  └───────────────┬───────────────────────┬───────────────┘  │
└──────────────────┼───────────────────────┼──────────────────┘
                   │ 1. 抢购成功 (入队)    │ 2. 状态查询 (读缓存)
                   ▼                       ▼
    ┌─────────────────────────┐  ┌────────────────────────┐
    │  Redis (Broker / Cache) │  │ Redis Order Status Key │
    └──────────────┬──────────┘  └────────────────────────┘
                   │ Celery 任务队列
                   ▼
┌─────────────────────────────────────────────────────────────┐
│                    Celery Worker Cluster                    │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ create_order_task (异步削峰落库)                       │  │
│  │ 1. 幂等性查重 (UUID 主键防重复消费)                     │  │
│  │ 2. PostgreSQL 插入待支付订单 & 物理库存 -1 (原子事务)  │  │
│  │ 3. 调度 cancel_unpaid_order_task (15分钟延时任务)      │  │
│  └───────────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ cancel_unpaid_order_task (超时取消与库存双向回滚)      │  │
│  │ 1. 检查订单状态是否仍为 PENDING                       │  │
│  │ 2. 更新订单为 CANCELLED & 数据库物理库存 +1            │  │
│  │ 3. 回滚 Redis 缓存: INCR stock + SREM users            │  │
│  └───────────────────────────────────────────────────────┘  │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
    ┌─────────────────────────────────────────────────────┐
    │              PostgreSQL 15 (物理持久化)             │
    │  - users (用户表)                                    │
    │  - products (商品表, 附带 stock >= 0 物理兜底约束)  │
    │  - orders (订单表, 状态: 待支付 / 已支付 / 已取消)   │
    └─────────────────────────────────────────────────────┘
```

---

## 🌟 核心特性 (Key Features)

### 1. Redis + Lua 内存原子扣减（绝对防超卖）
- 抢购接口严格遵循“禁止直接查询/更新数据库”原则。
- 将「单人限购校验（`SISMEMBER`）」+「库存是否充足（`GET`）」+「原子扣除（`DECR`）」+「记录购买用户（`SADD`）」封装在一个 Lua 脚本中原子执行，微秒级判定，杜绝网络时延导致的并发竞态。

### 2. Celery + Redis 异步削峰落库（保护持久层）
- 内存扣减成功后，接口向 Redis 队列发送异步落库任务并立即返回 HTTP 200，前端显示“排队中”。
- 后台 Celery Worker 以平稳受控速率消费任务，并在单事务内完成订单插入与数据库库存扣减（`UPDATE ... WHERE stock > 0` 兜底防超卖）。

### 3. Redis ZSET 滑动窗口限流（防刷防连点）
- 基于 Redis 有序集合（ZSET）记录请求毫秒时间戳，相较固定窗口算法彻底消除了“临界双倍突发”的缺陷。
- 支持单用户（User ID）与客户端真实 IP 双级降级限流，默认限制单秒最多 5 次请求，超额即刻返回 HTTP 429。

### 4. 超时未支付延时取消与双向回滚（最终一致性）
- 订单生成后自动挂载 15 分钟倒计时任务。
- 若用户超时未支付，自动将订单流转为 `CANCELLED`，不仅恢复 PostgreSQL 物理库存，还同步回滚 Redis 缓存库存并重置用户秒杀资格。

---

## 📁 目录结构规范 (Project Structure)

```text
flash-sale-lite/
├── docker-compose.yml          # PostgreSQL 15 & Redis 7 本地容器编排
├── requirements.txt            # 项目 Python 依赖列表
├── .env.example                # 环境变量模板
├── .env                        # 本地开发环境变量
├── README.md                   # 项目介绍主文档
├── alembic.ini                 # 数据库迁移配置
├── alembic/                    # Alembic 异步迁移脚本
├── learn/                      # 📖 面试讲解说明与核心考点手册
│   └── README.md
├── scripts/
│   ├── init_data.py            # 数据初始化脚本 (重置表、生成测试用户与商品、缓存预热)
│   └── benchmark.py            # 100 并发压测与数据对账验证脚本
└── app/
    ├── main.py                 # FastAPI 应用入口与 lifespan 资源管理
    ├── core/
    │   ├── config.py           # Pydantic Settings 配置驱动
    │   ├── database.py         # 异步引擎 (asyncpg) + 同步引擎 (psycopg2) 会话工厂
    │   └── redis.py            # Redis 异步连接池与规范化 Key 管理
    ├── models/                 # SQLAlchemy ORM 实体 (User, Product, Order)
    ├── schemas/                # Pydantic 请求参数验证与响应序列化
    ├── middlewares/
    │   └── rate_limiter.py     # 基于 Redis ZSET 的滑动窗口限流依赖项
    ├── services/
    │   ├── flash_sale.py       # 核心 Lua 抢购逻辑与 Celery 任务派发
    │   └── order.py            # 商品展示、库存预热与订单状态查询业务
    ├── api/
    │   └── v1/
    │       ├── api.py          # 路由聚合
    │       ├── products.py     # 商品增删改查与预热接口
    │       └── flash_sale.py   # 秒杀触发与订单状态轮询接口
    └── worker/
        ├── celery_app.py       # Celery 实例配置
        └── tasks.py            # 异步落库任务与超时取消延时任务
```

---

## 🚀 极简快速启动 (Quick Start)

### 1. 启动基础设施 (PostgreSQL & Redis)

如果本地已安装 Docker：
```bash
docker compose up -d
```
> 若本机已有 PostgreSQL (5432) 和 Redis (6379) 运行，可直接跳过 Docker，只需确保 `.env` 中的配置一致即可。

### 2. 安装 Python 环境依赖

```bash
# 创建并激活虚拟环境 (推荐 Python 3.11+)
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 3. 数据初始化与缓存预热

运行初始化脚本自动同步数据表结构、生成 100 位测试用户，并创建一个库存为 10 的测试商品同步预热至 Redis：
```bash
python scripts/init_data.py
```

### 4. 启动服务与后台 Worker

**终端 1：启动 Celery 异步落库 Worker**
```bash
celery -A app.worker.celery_app worker --loglevel=info --concurrency=4
```

**终端 2：启动 FastAPI Web 服务**
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
访问 Swagger API 交互文档：[http://localhost:8000/docs](http://localhost:8000/docs)

---

## 🧪 高并发压测与防超卖验证 (Benchmark)

在第三个终端执行内置的自动化并发压测脚本：
```bash
python scripts/benchmark.py
```

### 实测输出：
```text
======================================================================
      🚀 Flash Sale Lite 高并发秒杀防超卖压测验证开始 🚀
======================================================================
目标接口: http://127.0.0.1:8000/api/v1/flash-sale/buy
并发请求数: 100 个协程
测试商品 ID: 1 (初始库存: 10 件)

[阶段 1] 正在重置测试环境并调用接口对商品进行库存预热...
   ✓ 库存预热成功: PostgreSQL 物理库存已置为 10，Redis 缓存库存已同步置为 10，去重集合已重置。

[阶段 2] 正在瞬间发射 100 个并发秒杀请求 (asyncio + httpx)...
   ✓ 并发请求全部完成！耗时: 0.183 秒 (估算 QPS: 547.9 req/s)

[阶段 3] HTTP 响应分布统计:
   ├─ 抢购成功数 (HTTP 200):         10 次  ✓ (精准等于库存 10)
   ├─ 提示已售罄数 (HTTP 400):       90 次  ✓ (精准拦截 90 人)
   ├─ 重复购买拦截 (HTTP 400):        0 次
   ├─ 触发限流拦截 (HTTP 429):        0 次
   └─ 其他错误响应:                   0 次

[阶段 4] 等待 3 秒让 Celery Worker 异步消费队列并完成落库...
   ✓ 等待结束，开始验证底层数据库与缓存的一致性！          

[阶段 5] 正在校验 Redis 缓存与 PostgreSQL 数据库数据...
┌────────────────────────────────────────────────────────┐
│                   最终数据核验对照表                   │
├──────────────────────┬──────────┬──────────┬───────────┤
│ 核验指标             │ 预期数值 │ 实际数值 │ 判定结果  │
├──────────────────────┼──────────┼──────────┼───────────┤
│ 接口抢购成功总数     │    10    │     10   │  PASS ✓   │
│ Redis 剩余库存       │     0    │      0   │  PASS ✓   │
│ Redis 购买用户数     │    10    │     10   │  PASS ✓   │
│ PostgreSQL 订单总数  │    10    │     10   │  PASS ✓   │
│ PostgreSQL 剩余库存  │     0    │      0   │  PASS ✓   │
└──────────────────────┴──────────┴──────────┴───────────┘

======================================================================
 🎉【验证结论】：防超卖高并发验证全部通过 (ALL PASSED)！
   - 10 件库存精准售罄，无少卖、无多卖、无超卖！
   - Redis Lua 内存扣减与 PostgreSQL 异步落库严格保证最终一致性！
======================================================================
```

---

## 📚 学习与面试文档

- 核心高频面试题剖析（为什么不用 MySQL 行锁、为什么必须使用 Lua 脚本、FastAPI 异步与 Celery 同步引擎隔离等），请参阅：👉 [【learn/README.md】](learn/README.md)

---

## 📄 License

本项目采用 [MIT License](LICENSE) 开源协议。
