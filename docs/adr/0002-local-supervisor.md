# ADR 0002：M1 本机 supervisor 与持久控制面

[![中文](../assets/lang-zh.svg)](0002-local-supervisor.md) [![English](../assets/lang-en.svg)](0002-local-supervisor.en.md)

- 日期：2026-10-08
- 状态：Accepted，范围为单机可信任务

## 问题

Activity 可因执行后确认丢失而重试。直接把 Harbor/Codex 整轮调用放进 Activity，可能
重复修改工作区、重复调用模型。M0 的 async task 生命周期也不能保证 worker SIGKILL
后继续观察已有沙箱。

## 决策

Temporal 只负责短控制 Activity。PostgreSQL 保存产品身份和取消意图；周期核对弥补
提交事务与 Workflow start 的确认窗口。一个 Execution 对应一个独立本机 supervisor、
稳定 Compose project 名和本地产物目录。`flock` 与先写入的 start intent 防止同一
Execution 重入；死亡 supervisor 不自动重放。结果先落本地文件、清理外部资源，再以
数据库事务发布终态和事件；清理失败保留 pending。

Temporal 开发服务用持久 SQLite 文件，产品元数据仍按 ADR 0001 使用 PostgreSQL。
原始产物当前放本地忽略目录；对象存储留到 M2/M4。M0 会话恢复和接力探针保持独立，
本次不把它们包装成自动恢复策略。

## 取舍

保留 Harbor 公开任务 / agent / verifier 和大部分环境接口；固定 0.24.0 的三个内部
transport hooks 补齐默认超时与取消后回收，避免只取消本地 client 留下迟到操作。
这增加升级维护成本，以单测和真实取消实验约束。

文件锁是同主机内核协调机制，不是租约或跨主机 fencing。磁盘丢失、宿主机故障、
supervisor 在沙箱创建中硬杀导致的迟到资源、多主机并发接管不属于本次保证。保持
原始状态以便诊断，后续通过持久条件提交、epoch 和资源回收协议完善。旧 worker 恢复
与未决副作用的严格处理属于 M2；M1 不宣称模型调用或外部工具 exactly-once。

## 验证

[M1 指南与证据](../m1.md)包含真实 Activity 确认丢失、worker 重启、取消、TTL、
supervisor 丢失和单次真实 Codex 跨 worker 重启实验。机制复用 Temporal 的持久编排、
PostgreSQL 事务和 POSIX 文件锁；操作 ID、状态核对与执行边界由 RTV 设计实现。
