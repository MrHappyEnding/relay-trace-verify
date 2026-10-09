# ADR 0003：M2 条件检查点与执行所有权

[![中文](../assets/lang-zh.svg)](0003-fenced-checkpoints.md) [![English](../assets/lang-en.svg)](0003-fenced-checkpoints.en.md)

- 日期：2026-10-09
- 状态：Accepted，单机 runtime v2；扩展 ADR 0001 / 0002

## 问题

M1 可接管存活的 supervisor，却不能恢复丢失的执行。上传中断可能留下半成品；旧进程
失联后返回可能覆盖新结果；原生会话的累计用量可能重复计算前缀。文件存在、进程恢复、
语义接力和产品级成功是不同事实，需要明确权威边界。

## 决策

1. 保留 Trial / Attempt，阶段推进或恢复时创建新 Execution 与递增 epoch。每个
   Execution 独占目录和 Compose project，物理调用 operation ID 在调用前持久化，
   重入时拒绝重新调用模型。
2. PostgreSQL 保存当前指针和租约，使用数据库时钟。先锁定稳定的 Trial 行，再在
   新语句中读取当前 Execution；并发恢复只有一方可以推进所观察到的 epoch。
   状态、事件、检查点和结果发布都校验 expected execution / epoch / attempt。
3. 检查点采用 PREPARING → COMMITTED：停止沙箱，写入文件、manifest 和 fsync，
   独立恢复校验后条件提交 DB 指针与事件。不使用跨 PostgreSQL / 文件系统的分布式
   事务，也不把目录 rename 视为产品提交。v1 教学文件格式继续保留，runtime 使用 v2。
4. 同一 checkpoint ID 不覆盖已有文件。上传或 DB 确认丢失时核对同一 ID；若源执行
   已被替换则拒绝发布。完成边界已提交、结果确认丢失时，控制器可以读取提交记录完成
   核对。只从 COMMITTED 恢复，所有恢复再次检查文件内容和兼容性。
5. PENDING / KNOWN / UNKNOWN 账本按物理阶段调用累计。迟到用量是原操作的观测，
   可以结算原行；它不赋予旧执行发布结果的权限。恢复不清空历史，不把未知费用当零，
   不推算订阅美元成本。恢复次数与 TTL 限制独立，TTL 不重置。
6. 显式恢复 key 保证重复请求不会递增两次 epoch。自动恢复仅适用于 supervisor 丢失
   或租约过期，要求既有提交和显式额度；容器 / adapter 错误保留手动恢复入口。产品
   已终态不重放；合法手动恢复先更新产品状态，再允许 Temporal 新建控制 run。
7. GC 先锁定元数据、核对隐式和显式引用、写删除墓碑，再删除文件。新 pin / commit /
   restore 拒绝已删除对象。删除失败或旧上传迟到时重复 sweep。终态 Trial 的隐式
   引用只能显式 retire；墓碑保留，镜像和原始执行日志另行管理。

## 取舍

采用单机本地文件存储，避免在恢复语义尚未稳定时引入对象存储服务。fsync 与 hash
检查提高提交可解释性，但未验证断电、磁盘损坏恢复、跨主机存储或分布式资源 fencing。
DB 租约约束权威写入，不能使任意外部 API 具备 exactly-once。旧沙箱文件通过独立目录
隔离，清理由协调器持续按执行身份核对。

检查点需要静止整个沙箱，恢复粒度为已完成阶段。中间工具进程及其文件修改会丢弃；
当前并不截获 Codex 每个内部工具操作。实现选择保守的快照与累计账本，后续有测量依据
再考虑增量快照、细粒度工具协议和并发扫描优化。

runtime v2 使用独立 `rtv2` schema、默认 root 与任务队列，防止 M1 的活动进程解释
新协议。不自动迁移历史；旧 M1 可在提交 `0b46ea4` 复现。真实实验分支、第二 adapter
和多主机生命周期协议分别留给 M3 / M4。

## 验证与来源

验收分为真实服务 / 容器 fixture、真实数据库 / 文件系统上的合成协议故障，以及两次
真实 Codex 订阅调用，详见 [M2 指南](../m2.md)。测试发现并修复了并发指针更新时的
锁定 join 读不到当前行，以及手动恢复后已关闭 Workflow 不重新启动的问题。

- [PostgreSQL 显式锁](https://www.postgresql.org/docs/17/explicit-locking.html)：复用行锁和事务序列化产品状态变更。
- [PostgreSQL 约束](https://www.postgresql.org/docs/17/ddl-constraints.html)：复用唯一约束保障操作身份与 epoch 唯一性。
- [Temporal Activities](https://docs.temporal.io/activity-definition)：复用持久控制重试；物理模型调用不放入可重放 Activity。
- [Harbor trajectory loading](https://docs.harborframework.com/jobs/loading-trajectories)：复用 adapter 会话加载；工作区快照、所有权和费用语义由 RTV 补齐。

这些是对已有事务、租约、幂等操作和 checkpoint 机制的学习应用。RTV 的实现贡献在
跨层边界、协议组合及可复现故障验证；不把这些通用机制宣称为新算法。
