# RTV 系统设计提案

[![中文](assets/lang-zh.svg)](design.md) [![English](assets/lang-en.svg)](design.en.md)

状态：Draft · 2026-10-07

## 1. 目标与保证范围

构建支持一致性检查点、异构 agent 接力和分支实验的执行系统。第一版只支持能够在声明的静止边界停止写入、且环境可由固定镜像和声明文件重建的任务。

承诺恢复到最后一个已提交的应用级检查点。尚未提交的工作可能丢失；损失窗口必须可观测。检查点不保存任意进程内存、网络连接或未声明的外部服务状态。

复现指配置、输入和产物可追溯，环境满足恢复契约；不承诺模型输出逐字确定。

## 2. 状态对象

| 对象 | 定义 | 关系及标识 |
| --- | --- | --- |
| Experiment | 固定任务集合、策略、预算口径及重复次数的实验 | `experiment_id`，不可变配置 digest |
| Trial | 某任务在某个实验分支与重复编号下的完整求解过程 | `trial_id`，拥有累计预算和最终结果 |
| Attempt | 由一套 agent / model / scaffold 配置负责的逻辑执行阶段 | `attempt_id`；一个 Trial 可有多个 Attempt |
| Execution | Attempt 的一次物理执行实例 | `execution_id`、单调递增 `epoch`、独立 sandbox |
| Checkpoint | 已静止状态的一组不可变产物及 manifest | `checkpoint_id`、digest、来源执行与事件边界 |
| Handoff | 从源 Attempt 的检查点到目标 Attempt 的持久化交接操作 | `handoff_id`，绑定源检查点及目标配置 |
| Operation | 创建沙箱、启动、终止、提交等可核对操作 | 稳定 `operation_id`，重试不换 ID |
| Event | 控制面或执行过程的持久记录 | 来源执行、来源序号、类型、时间与产物引用 |

**恢复**保留 Attempt ID，创建新的 Execution / epoch。**接力**创建新的 Attempt。**实验分叉**创建新的 Trial 与独立预算账本，记录 `parent_checkpoint_id` 和前缀成本；它不是原 Trial 的自动重试。

不得直接把 Temporal Activity attempt number 当作产品层 Attempt ID。

## 3. 必须维持的不变量

1. 只有已提交、内容校验通过的检查点能成为恢复 / 交接输入。
2. 同一 Attempt 只有当前 epoch 能提交权威状态与有效结果。
3. 不同 Execution 默认使用独立可写工作区；实验分支不能共享可写目录。
4. 取消一旦生效，禁止新的执行准入与 agent 阶段启动；清理可以异步重试。
5. Trial 的成本账本跨恢复和接力累计，不因 Execution 重建而清零。
6. 每个有效结果都能追溯到任务版本、执行配置、工作区产物及验证器版本。
7. 结果状态与资源清理状态分开记录；结束不意味着沙箱已删除。

## 4. 状态所有权

Temporal 持有工作流执行历史；PostgreSQL 持有产品对象、取消意图、ownership epoch、预算准入和产物提交记录。工作流通过具备稳定操作 ID 的 Activity 访问这些记录，不在 Workflow 代码中直接做 I/O。

不使用数据库和 Temporal 的双写来假设分布式原子性。数据库先提交的操作结果可被重试查询；尚未完成的跨系统操作由 reconciliation 继续核对。API 的状态展示以产品状态与明确的同步时间为准。

最小状态示意：

```text
Trial: QUEUED -> RUNNING -> VERIFYING -> SUCCEEDED | FAILED
          |         |          |
          +---------+----------+-> CANCEL_REQUESTED -> CANCELLED

Execution: ALLOCATING -> RESTORING -> RUNNING -> QUIESCING -> STOPPED
                       \-------------> LOST | FAILED

Checkpoint: PREPARING -> UPLOADING -> COMMITTED
                      \-----------> ABORTED

Handoff: REQUESTED -> SOURCE_SEALED -> TARGET_READY -> COMPLETED
                   \------------------------------> ABORTED
```

状态迁移采用条件更新并检查 epoch / 取消标志。失败分类是结构化字段，不把所有超时合并为 agent 失败。

## 5. 检查点提交协议

### 5.1 安全边界

优先支持阶段结束时的 checkpoint；工具边界 checkpoint 只有在 adapter 可证明静止时开启。仅停止 agent 主进程不足以证明没有子进程或后台服务写入。

静止要求：停止新工具请求；等待或终止当前工具与相关子进程；刷新受控日志；确认声明的文件范围不再写入。不能确认时拒绝提交新 checkpoint，并保留最后一个有效 checkpoint。

原型应验证使用进程组 / sandbox 生命周期完成静止是否足够；不支持的任务必须明确失败。

### 5.2 Manifest 内容

- Schema version、checkpoint ID、来源 Trial / Attempt / Execution / epoch。
- Task digest、起始代码版本、镜像 digest、环境与恢复配置 digest。
- 声明文件范围内的快照清单、文件内容 hash、权限和符号链接语义。
- 会话快照引用、格式、agent / scaffold 版本、转换器版本及已知信息损失。
- 已确认工具记录与执行事件的完整边界，不仅记录最大序号。
- 公开测试结果引用及其对应工作区 digest；禁止把旧版本测试结果当作当前结论。
- 预算账本 revision、已消费、预留、待核对记录的引用。
- 父检查点与创建原因。

单独的 Git diff 不构成完整文件快照。新增文件、必要生成物与声明挂载必须覆盖；凭据通过运行时重新注入，不写入可移植产物。

### 5.3 提交步骤

1. 核对当前执行所有权与取消意图，进入 QUIESCING。
2. 完成静止屏障，确定文件 / 会话 / 事件的共同边界。
3. 上传不可变文件、会话和事件产物，计算并验证 digest。
4. 上传不可变 manifest，校验其全部引用可读且一致。
5. 在数据库事务中条件提交 checkpoint，并更新相应指针；检查 epoch 与取消标志。
6. 返回稳定操作结果；若响应丢失，通过同一 operation ID 读取已提交记录。

对象存储与数据库不需要跨系统事务：只有第 5 步成功的产物才可恢复。未引用产物经保留期后回收；GC 必须保护正在提交的上传会话和所有存活分支引用。

## 6. 恢复与接力

### 6.1 恢复

1. 确认故障类别；基础设施重试与 agent 继续尝试使用不同策略。
2. 原子申请新 epoch，创建带稳定操作 ID 的新沙箱。
3. 校验 manifest，重建镜像与声明文件，验证工作区 digest。
4. 导入原生会话；不支持时只能按明确标记的语义恢复策略处理。
5. 核对未决 Operation 与预算；无法确认完成的动作记录为 UNKNOWN。
6. 再次检查取消状态后启动 agent。

旧执行即使恢复联网，也只能产生过期观测，不能提交有效状态。epoch 校验不能阻止磁盘写入，所以旧执行和新执行禁止共享可写工作区。终止与回收旧沙箱持续由独立 reconciliation 处理。

### 6.2 接力

接力由 source checkpoint、目标配置、上下文传递策略与预算上限共同定义。目标准备完毕并验证恢复状态后，再提交控制权迁移。重复投递同一 handoff ID 不能创建第二个有效目标 Attempt。

交接上下文支持：任务说明；可移植轨迹；结构化摘要。摘要必须区分观察事实、测试证据、已尝试方案和 agent 推测，并引用原始事件。摘要生成和转换消耗计入总预算。

agent 或模型变化不隐含工具兼容、原生会话兼容或相同上下文语义。

## 7. Adapter 能力契约

以下是需要探测和测试的能力，不是对某个 agent 当前实现的保证。

| 能力 | 第一阶段策略 |
| --- | --- |
| 启动、结果收集、状态核对 | 必需；统一退出状态和错误类别 |
| 进程组终止与静止证明 | 必需；无法证明时拒绝 checkpoint |
| 阶段边界 checkpoint | 必需 |
| 工具边界 checkpoint | 可选，后续扩展 |
| 原生会话导出 / 导入 | 显式声明，受版本兼容性约束 |
| ATIF 输出 / 加载 | 分别声明，不把能输出视作能导入 |
| 增量事件与 token / 费用统计 | 标注完整度和估算来源 |
| 请求费用上界 / 调用拦截 | 支持时提供强准入限制，否则声明软限额 |

首个已验证对象为 Codex CLI 0.160.1 + `gpt-6-astra`，通过 Harbor 0.24.0 接入，见[实验记录](codex-m0.md)。M3 第二种 agent 已固定为 mini-swe-agent 2.4.6，脚本模型执行循环已验证，真实 API 验收待运行，见 [M3](m3.md)。先做阶段边界交接，避免假设任意 CLI 能在任意工具调用后暂停。

## 8. 预算模型

Trial ledger 包含 `reserved`、`settled`、`unknown` 三类记录，模型费用与基础设施费用分列。每个 operation 的账本更新幂等；真实发出的重复请求必须分别记账，不能通过去重隐藏实际开销。

在可拦截调用的 adapter 中，先预留请求费用上界，再发送请求，收到 usage 后结算并释放差额。响应丢失时保留待核对额度，不能自动当作零消耗。

对无法拦截内部请求的 CLI，预算仅能通过原生限制、观测与终止实现软控制。文档与指标必须报告可能超出的窗口；不能宣称严格金额上限。

基础设施额度采用资源配额和运行 TTL 约束，账单属于延迟观测。用户取消后，在途请求仍可能产生费用。

## 9. 故障处理矩阵

| 注入位置 / 故障 | 预期处理 | 核心断言 |
| --- | --- | --- |
| 沙箱创建完成但确认丢失 | 按 operation ID 查询并接管 | 不产生第二个有效执行 |
| 文件上传中断 | checkpoint 保持未提交 | 恢复端看不到半成品 |
| checkpoint 数据库提交后响应丢失 | 重试读回同一结果 | 不重复推进状态 |
| worker 心跳超时后旧 worker 回归 | epoch fencing，旧结果隔离 | 旧代次不能发布 |
| 容器销毁 | 新沙箱恢复已提交 checkpoint | 声明文件与会话契约一致 |
| 取消与启动 / 接力并发 | 取消意图在准入点持续检查 | 不开始新有效阶段，最终清理 |
| 模型响应丢失 | UNKNOWN，按策略核对或受控重试 | 费用不被错误释放 |
| 隐藏验证器失败 / 超时 | 独立分类并限次重试 | 不直接算作 agent 解题失败 |

## 10. 事件与诊断

控制事件包含接收、准入、启动、失联、取消、checkpoint、handoff、验证和清理；工具事件关联调用与结果。使用来源序号与幂等键处理重复投递，不依赖跨机器 wall clock 建立全局顺序。

原始产物不可变，查询投影可重建。大日志放对象存储，不写入 Temporal history。先做失败分类、重复操作、公开测试变化与恢复损失窗口；过程 judge 后续校准。

## 11. 依赖核验入口

- [Harbor loading trajectories](https://docs.harborframework.com/jobs/loading-trajectories)
- [Harbor multi-step tasks](https://docs.harborframework.com/tasks/multi-step)
- [Harbor separate verifier](https://docs.harborframework.com/tasks/separate-verifier)
- [Temporal Activity definition](https://docs.temporal.io/activity-definition)

上述文档是调研依据。设计中的静止、快照和 ownership 协议为本项目提案，需要实测验证。
