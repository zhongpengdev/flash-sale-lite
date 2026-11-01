# flash-sale-lite

轻量级高并发秒杀与订单履约系统，基于 FastAPI、Redis、Celery 与 PostgreSQL 构建。核心聚焦于高并发场景下的四个经典问题：**Redis + Lua 原子防超卖**、**Celery 异步削峰**、**滑动窗口接口限流**以及**超时未支付延时回滚**。

详细的架构推演与高频面试设计解析，请见 [learn/README.md](learn/README.md)。

---

## 架构简图

```text
[ Client / Benchmark ]
         │ (HTTP 并发请求)
         ▼
[ FastAPI (Web Layer) ]
  ├─ RateLimiter (Redis ZSET 滑动窗口限流: 5 次/秒)
  └─ FlashSaleService (Redis Lua 原子脚本)
       ├─ SISMEMBER: 用户防重校验
       ├─ GET & DECR: 检查并原子扣减库存
       └─ SADD: 记录已购用户
         │
         ├─ 成功: 投递 Celery 异步落库任务
         └─ 售罄 / 重复: 直接返回 400
         │
         ▼
[ Redis (Broker & Cache) ]
         │
         ▼
[ Celery Worker ]
  ├─ create_order_task (异步落库)
  │    ├─ UUID 幂等校验
  │    ├─ 插入 orders 待支付订单
  │    ├─ 原子扣减 products 物理库存 (UPDATE ... WHERE stock > 0)
  │    └─ 调度 15 分钟延时任务
  │
  └─ cancel_unpaid_order_task (超时未支付取消)
       ├─ 检查订单状态是否仍为 PENDING
       ├─ 订单状态变更为 CANCELLED
       ├─ PostgreSQL 物理库存 +1
       └─ Redis 缓存库存回滚 (INCR stock, SREM users)
         │
         ▼
[ PostgreSQL (物理持久化) ]
```

---

## 核心机制

### 1. 原子扣减库存与防重 (Redis + Lua)
抢购接口不直连数据库。通过 Redis 执行单个 Lua 脚本，在内存中原子化完成“单人限购校验”、“剩余库存判断”、“库存递减”与“记录用户已购”。脚本单线程执行，避免网络往返导致的竞态条件，杜绝超卖。

### 2. 异步削峰落库 (Celery)
抢购成功后向 Celery 投递任务并立即响应前端，将瞬时并发写转化为受控的后台消费。Worker 在落库时以订单 UUID 为幂等键，并通过带有条件约束的 SQL（`WHERE stock > 0`）进行数据库行级物理兜底。

### 3. 超时未支付取消与库存回滚
订单生成后挂载 15 分钟延时任务。若用户超时未支付，自动将订单状态变更为已取消，并同时恢复 PostgreSQL 物理库存与 Redis 缓存库存，移出已购用户集合，保证最终一致性。

### 4. 接口滑动窗口限流 (Redis ZSET)
基于 Redis 有序集合记录请求时间戳毫秒数，动态移除当前窗口之前的过期记录并统计频次。相比固定窗口计数器，有效避免了边界临界双倍突发流量的问题。

---

## 快速开始

### 1. 启动依赖服务 (PostgreSQL & Redis)

```bash
docker compose up -d
```

### 2. 安装 Python 依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. 数据初始化与缓存预热

运行初始化脚本，自动创建数据表、生成 100 位测试用户，并将库存为 10 件的测试商品预热至 Redis：

```bash
python scripts/init_data.py
```

### 4. 启动服务与后台 Worker

```bash
# 终端 1: 启动 Celery Worker
celery -A app.worker.celery_app worker --loglevel=info --concurrency=4

# 终端 2: 启动 FastAPI
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- 接口文档：`http://localhost:8000/docs`
- 健康检查：`http://localhost:8000/health`

---

## 并发压测与验证

项目提供开箱即用的压测验证脚本，模拟 100 个并发协程抢购 10 件库存的测试商品：

```bash
python scripts/benchmark.py
```

### 验证输出示例

```text
======================================================================
      Flash Sale Lite 并发秒杀防超卖压测验证
======================================================================
目标接口: http://127.0.0.1:8000/api/v1/flash-sale/buy
并发请求数: 100
测试商品 ID: 1 (初始库存: 10 件)

[阶段 1] 重置环境并执行库存预热完成。
[阶段 2] 发起 100 个并发请求完成，耗时: 0.183 秒 (QPS: 547.9)。

[阶段 3] HTTP 响应分布统计:
   - 抢购成功数 (HTTP 200):  10 次 (等于库存 10)
   - 提示已售罄数 (HTTP 400): 90 次 (拦截 90 人)
   - 重复购买拦截 (HTTP 400):  0 次
   - 限流拦截 (HTTP 429):     0 次

[阶段 4] 等待 3 秒让 Celery Worker 消费完毕...

[阶段 5] 数据一致性对照表:
+----------------------+----------+----------+----------+
| 核验指标             | 预期数值 | 实际数值 | 判定结果 |
+----------------------+----------+----------+----------+
| 接口抢购成功总数     |    10    |    10    | PASS     |
| Redis 剩余库存       |     0    |     0    | PASS     |
| Redis 购买用户数     |    10    |    10    | PASS     |
| PostgreSQL 订单总数  |    10    |    10    | PASS     |
| PostgreSQL 剩余库存  |     0    |     0    | PASS     |
+----------------------+----------+----------+----------+

结论: 防超卖验证全部通过，无超卖、无少卖，缓存与数据库最终一致。
======================================================================
```

---

## 目录结构

```text
.
├── docker-compose.yml          # 本地依赖服务配置
├── requirements.txt            # Python 依赖
├── .env.example                # 环境变量模板
├── README.md                   # 项目说明
├── alembic/                    # 数据库迁移脚本
├── learn/                      # 架构推演与面试题解
│   └── README.md
├── scripts/
│   ├── init_data.py            # 数据初始化脚本
│   └── benchmark.py            # 并发压测验证脚本
└── app/
    ├── main.py                 # FastAPI 入口与生命周期管理
    ├── core/                   # 配置、数据库引擎与 Redis 连接池
    ├── models/                 # SQLAlchemy ORM 实体
    ├── schemas/                # Pydantic 参数校验
    ├── middlewares/            # 滑动窗口限流中间件
    ├── services/               # 秒杀 Lua 业务与订单服务
    ├── api/                    # 路由层
    └── worker/                 # Celery 异步任务
```
