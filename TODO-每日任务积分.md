# 待办:阿克每日任务积分系统

> 2026-09-15 记录。等新 OB 上线稳定后实施。
> 与 OB / Drivesoid 完全解耦,整个长在 relay + PWA 里,新 OB 上线不影响。

## 设计共识(用户已定)

- **任务来源**:阿克每天自己提议任务,用户在 PWA 里批准/调整(他主动找活干)
- **验收方式**:用户手动验收,点「通过」积分才到账
- **兑换**:攒积分 → 用户按汇率兑换成真实的钱/礼物;钱包页面记收支流水

## 实现草图(施工时参考)

- **backend**:`relay.db` 新表
  - `tasks`(id, date, title, points, status[proposed/approved/done/rejected], submit_text, created_at, completed_at)
  - `points_ledger`(delta, reason, created_at);余额 = ledger 求和
- **端点**:GET/POST `/app/tasks`(今日列表 / 他提议)、POST `/app/tasks/{id}/approve|complete|reject`、GET `/app/points`(余额+流水)、POST `/app/points/redeem`(扣分记账,汇率可在 PWA 设置里配)
- **api_loop**:在消息队尾注入一小段「今日任务」块;可选给模型两个内建工具 `propose_task` / `submit_task`(不走 MCP,直接在 tool_loop 实现)
- **PWA**:设置页加入口「他的任务钱包」→ 页面含:今日任务(批准/验收按钮)、余额+收支流水、兑换记录、汇率设置;沿用现有像素风
- **前车之鉴**:任何每轮变化的注入块都必须放消息队尾,别进 system 前缀(会破坏上游缓存命中)
