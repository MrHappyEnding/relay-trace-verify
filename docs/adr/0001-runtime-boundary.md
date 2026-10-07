# ADR 0001：Runtime 与已有框架的边界

- 日期：2026-10-07
- 状态：Proposed

## 背景

项目希望解决长程 Coding Agent 在中断、交接和分支实验中的状态一致性问题。Harbor 已经提供轨迹格式、部分跨 agent 轨迹加载、多阶段任务环境延续和托管执行；Temporal 已经提供持久化编排。因此仅封装运行 API 或串联两个 agent 无法充分表达项目的技术贡献。

## 提案

1. 复用 Harbor 的任务、agent / sandbox 适配、验证器与 ATIF；具体集成接口由 M0 确认。
2. 使用 Temporal 编排控制流程，Activity 输入 / 输出使用小型引用，原始大产物存放对象存储。
3. 自研 checkpoint 提交、ownership / epoch、恢复与接力协议、预算语义和分支实验。
4. PostgreSQL 维护产品对象与条件更新。通过稳定操作 ID 和 reconciliation 解决跨系统确认丢失，不假设外部副作用 exactly-once。
5. 每个资源只由一层负责创建、接管和清理，禁止 Harbor job 调度器与上层调度器独立重试同一执行。
6. 第一阶段只保证阶段边界应用级恢复；工具边界恢复需要明确 adapter 支持。

## 备选及取舍

| 方案 | 优点 | 代价 / 结论 |
| --- | --- | --- |
| 只包装 Harbor CLI | 最快得到端到端运行 | 难以精确控制中断与状态边界，适合探针而非直接认定最终方案 |
| 从零实现评测与 agent 框架 | 完全控制 | 重复已有能力，维护成本过高 |
| 自研持久工作流引擎 | 可深入通用调度 | 偏离核心问题，先复用 Temporal |
| 直接实现任意进程快照 | 保留更多系统状态 | 可移植性与兼容性复杂，超出初始恢复契约 |

## 后果

需要对依赖版本和 agent 能力做契约测试，并坦诚记录原生 / 可移植会话转换的信息损失。项目成效分别由故障注入和公平实验验证，不能把依赖框架已有能力算作自研实现。

## 接受条件

M0 证明在选定接入层可以控制沙箱生命周期、阶段边界静止、会话导出 / 导入和验证器调用。若不满足，补充 ADR 调整集成方式，并保留本决策的历史。

## 来源

- https://docs.harborframework.com/jobs/loading-trajectories
- https://docs.harborframework.com/tasks/multi-step
- https://docs.harborframework.com/hosted-harbor
- https://docs.temporal.io/activity-definition
