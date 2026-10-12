# ADR 0005：Kubernetes 执行身份、资源预约与恢复

[![中文](../assets/lang-zh.svg)](0005-kubernetes-executions.md) [![English](../assets/lang-en.svg)](0005-kubernetes-executions.en.md)

状态：M4 第一阶段实现；本机验收范围见 [M4 指南](../m4.md)。

## 问题与参考

M3 的宿主机 supervisor 与文件锁不能直接充当跨节点执行器。控制 worker 重启、
Kubernetes API 回执丢失、执行节点失联是三个不同故障。直接重试启动命令可能重复运行
agent；仅凭租约超时释放资源则可能在旧执行仍存活时超额准入。

复用 [Kubernetes Job](https://kubernetes.io/docs/concepts/workloads/controllers/job/)
的 Pod 生命周期和 [Kubernetes 资源配额](https://kubernetes.io/docs/concepts/policy/resource-quotas/)，
[Temporal Activity](https://docs.temporal.io/activity-definition) 负责可重试控制流程，
[PostgreSQL advisory locks](https://www.postgresql.org/docs/17/explicit-locking.html#ADVISORY-LOCKS)
提供跨进程互斥。Harbor 0.24.0 的 DockerEnvironment 与 DinD 思路用于保留已有沙箱和 verifier；
固定源码中的执行、停止、导出接口由 M0–M3 的契约测试覆盖。这些能力归属于上游；
RTV 的适配是执行身份、预约账本、checkpoint / epoch 与 Job 的关联和故障验收。

## 决策

1. `rtv4` 使用独立 PostgreSQL schema、Temporal 队列和 Workflow ID 前缀，M3 默认行为保留。
   一个物理 Execution 对应一个稳定名称的 Job。Job 内启动私有 Docker daemon 和 supervisor，
   Harbor 继续管理内部任务容器。Job/Pod 由 Kubernetes 管；RTV 只发送幂等创建/删除意图。
2. Job 名称、epoch、任务规格、固定镜像、原始截止时间和恢复时避开的节点形成契约摘要。
   创建响应丢失后查询同名对象；不同契约拒绝接管。删除使用 UID precondition，
   防止删除同名替代对象。`backoffLimit=0`、`restartPolicy=Never`，并保留 PostgreSQL
   `claim`/调用准入，不能将 Kubernetes Job 理解为 exactly-once 模型执行保证。
3. 同一运行根目录是一个资源池。PostgreSQL 事务串行化 jobs/CPU/内存预约；多个控制
   worker 使用数据库 advisory lock，不依赖共享文件锁。租约到期不释放预约；
   只有观察到 Job 和 Pod 都不存在才释放。namespace ResourceQuota 再限制物理资源。
4. 故障恢复创建新 Execution、Job 和可写目录，保持 Attempt 与绝对 TTL。
   只能使用已提交且内容校验通过的 checkpoint。新预约记录旧执行节点并禁止在那里调度，
   避免节点刚失联但 Kubernetes 尚未标记 NotReady 的窗口。旧预约等待正常删除回执；
   不自动 force-delete 不可达节点的 Pod，因此容量不足时恢复可以等待。
5. 旧 epoch 不能发布结果、checkpoint 或新的工具调用。已经发出的外部操作不能撤回；
   未决调用保留 UNKNOWN，恢复不将它变成零费用或自动结算。每次恢复重新检查工作区摘要。
6. 部署固定镜像 digest、依赖锁和镜像归档 SHA256；归档校验先于 daemon 启动。
   Docker Desktop 与 Job 内 daemon 都使用 containerd image store，防止经典 image store
   对同一镜像呈现不同 ID。参见 [Docker image store](https://docs.docker.com/engine/storage/containerd/)。
7. 第一阶段 runner 只允许 `fixture` / `mini-fixture`，没有 API key 挂载或真实模型入口。
   kubeconfig 位于共享产物目录之外；Job 不挂载宿主 Docker socket 或服务账号 token。
   supervisor 日志、任务结果及恢复产物保存在共享目录，清理 Job 后仍可诊断。

## 有意保留的限制

本次是单台 Docker Desktop 上的 k3d：1 个控制节点、2 个执行节点、同宿主 bind volume。
这能验证多个 Kubernetes 节点进程之间的调度、租约和恢复，不能证明多台机器、宿主断电、
PostgreSQL 高可用或对象存储持久性。Temporal 和控制 worker 仍在宿主运行。

DinD 的外层 Pod 是 privileged；该部署只面向可信本机开发任务，不是多租户安全沙箱。
元数据连接使用固定的本机开发凭据。正式部署需要 Secrets、权限/网络隔离、共享持久存储、
数据库高可用和镜像分发方案，不能直接沿用本例。

资源池状态按运行根目录隔离，物理 namespace 配额全局生效；第一阶段不承诺公平调度。
修复循环仍扫描该根目录历史，适合小规模验收，后续需要游标与归档策略。
报告区分故障时间、恢复 epoch 创建、claim、workspace restore；统计仅描述当前样本。
正式任务冻结、真实模型配对实验、置信区间和策略效果仍属于后续 M4 工作。
