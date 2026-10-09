# RTV 实施路线与验收

[![中文](assets/lang-zh.svg)](roadmap.md) [![English](assets/lang-en.svg)](roadmap.en.md)

状态：F0、首个 Codex 适配器 M0、单机范围 M1 验收完成 · 2026-10-08

首版框架已完成 F0，M0 已在首个 Codex 适配器的限定范围完成；M1 已在单机范围实现；M2–M4 尚未实现，顺序按风险消除安排。本地 fixture 和模拟协议测试不能替代实际容器及真实 agent 兼容性验证。参考复现与自主实现均保留，详见[学习计划](learning-plan.md)。

## F0：框架骨架

- [x] Python 包、`rtv` CLI、uv 依赖锁定。
- [x] Trial 状态迁移、Attempt / Execution 身份与适配器接口。
- [x] 本地文件检查点、哈希验证、独立目录恢复。
- [x] JSONL 事件、两阶段 fixture 接力、最终验证报告。
- [x] 状态、隔离、损坏检测与提交失败窗口测试。

当前实现边界见[框架开发指南](framework.md)。

## M0：兼容性与协议原型

- [x] 固定 Harbor 0.24.0 和基础镜像 digest；真实 Task parser 校验最小 fixture。
- [x] 实现只读预检查、文件恢复原型与模拟故障测试，提供实际容器测试入口。
- [x] 固定首个 agent：Codex CLI 0.160.1、`gpt-6-astra` 与本机 Docker 版本；第二种 agent 到 M3 再固定。
- [x] 跑通脚本 fixture 的真实容器文件恢复，验证 Harbor Task / sandbox / verifier 接入。
- [x] 跑通首个真实 agent 任务；复用 Harbor Codex adapter，RTV 控制阶段与检查点。
- [x] 验证阶段结束后工作区导出、新沙箱恢复和固定版本的原生会话导入。
- [x] 验证同版本 / 同模型 Codex 的文本 ATIF 导入和转换损失；跨框架验证留给 M3。
- [x] 真实工具及其子进程运行中取消，确认沙箱停止并回滚；`rtv capabilities` 声明范围。
- [x] 明确工作区 / 原生会话范围、普通文件权限、拒绝链接及生成物规则；保留检查点镜像。
- [x] 当前 Codex 只能软控制；中断用量 UNKNOWN，强 token / 金额准入不支持。

**验收证据**：两个隔离沙箱之间的恢复示例、文件 digest 对照、兼容性记录，以及明确拒绝不支持能力的行为。M0 不要求复杂网页或 Kubernetes。

当前命令、协议和证据见 [M0 指南](m0.md)。脚本容器往返及首个真实 Codex 的原生恢复 / 新会话交接均已通过，见 [Codex 证据](codex-m0.md)；后续 ATIF 与真实取消 / 回滚证据见 [M0 验收](m0-boundaries.md)。进程内存恢复与严格预算仍不支持；worker 重启接管见 [M1](m1.md)。

## M1：单 agent 持久执行

- [x] Trial / Attempt / Execution 对象、状态迁移与稳定 operation ID。
- [x] Temporal Workflow 与可查询、可重复调用的控制 Activities。
- [x] 提交、状态查询、取消、最终验证与产物保存。
- [x] 从第一天关联控制事件、执行事件、成本与结果。
- [x] worker 重启后的核对与沙箱接管，TTL 和清理重试。

**验收证据**：重复提交不会创建重复有效执行；取消能收敛；重启后可核对已有沙箱；测试结束后无遗留任务资源。

实际实现、22 项真实服务检查和 7 项真实 Codex 检查见 [M1 验收](m1.md)。范围为单机存活 supervisor 的接管，不包含丢失执行的自动重放。

## M2：检查点与可靠恢复

- [ ] manifest schema、内容校验、静止屏障与不可变产物存储。
- [ ] checkpoint 条件提交、epoch fencing 与独立工作区恢复。
- [ ] 未决工具操作与费用 UNKNOWN 状态处理。
- [ ] 提交窗口、失联与旧 worker 回归故障注入。
- [ ] 未引用产物回收和分支引用保护。

**验收证据**：半成品不可恢复；已提交文件恢复后一致；旧 epoch 不能发布；成本累计不重置；恢复损失窗口有记录。

## M3：双 agent 接力与实验分叉

- [ ] 幂等 Handoff、目标准备与控制权迁移。
- [ ] task-only / summary / portable trajectory 上下文策略。
- [ ] 从同一个 checkpoint 创建隔离分支。
- [ ] 配对实验、前缀成本归因与独立验证。
- [ ] 最小界面展示状态、checkpoint lineage、交接轨迹和结果。

**验收证据**：A 继续、B-workspace、B-summary 三分支可复跑；分支无文件污染；来源状态、成本和最终验证能完整追溯。

## M4：多 worker 验证与完整报告

- [ ] Kubernetes 或选定远程执行后端；每种资源只有一个生命周期 owner。
- [ ] 有界并发、资源准入、节点 / worker 丢失后的恢复及清理。
- [ ] 预先冻结正式任务、预算、排除规则与统计方法。
- [ ] 运行真实模型实验和系统故障实验，生成原始产物与报告。
- [ ] 记录部署步骤、运行成本、已知限制和完整演示。

**验收证据**：从配置可以重复执行实验；可靠性与策略效果分别有数据；所有对外效果数字能定位到原始记录。

## 后续候选

工具边界 checkpoint、更多 agent、经过标注校准的轨迹 judge、训练片段筛选、学习式接力策略。它们不阻塞 M0–M4。

## 开始编码前需要收敛的决策

| 决策 | 初始倾向 | 决定依据 |
| --- | --- | --- |
| Harbor 接入层级 | 尽量复用公开 agent / environment 接口 | M0 验证生命周期控制能力 |
| 调试任务集 | 小型、可重建、文件系统任务 | 恢复契约清楚，试验成本可控 |
| 文件快照 | 完整声明目录＋不可变 manifest | 先保证正确性，再测量增量优化收益 |
| 主要预算约束 | 费用与 wall clock 分开配置 | adapter 的准入与观测能力 |
| 控制语言 | Python | 与 Harbor 接入成本及类型契约 |
| 首个执行后端 | 本地容器 | 先验证协议，再验证分布式故障 |

下一项实际工作是 M2：持久条件提交、epoch fencing、检查点恢复和未决用量归因；第二种 agent 仍放在 M3。
