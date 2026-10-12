# M4：模型凭据通道与首个真实仓库试验

[![中文](assets/lang-zh.svg)](m4-model-pilot.md) [![English](assets/lang-en.svg)](m4-model-pilot.en.md)

**状态：修复版第二轮通过 13 项真实模型流程验收。** 一个 SWE-bench 开发任务完成检查点、
双 agent 接力、Codex 原会话继续和四分支独立判分；整个 M4 与正式效果评测仍未完成。
本轮 13 次 API 请求记录 $0.02641425，两轮累计 19 次请求、**$0.04215240**，计入同一 $8 总预算。
Codex 订阅美元成本未知，没有使用 GPU 或云资源。
见[第二轮证据](evidence/m4-real-pilot-2-arm64-20261012.json)；
[首轮失败与修复](evidence/m4-real-pilot-1-arm64-20261012.json)及
[假凭据检查](evidence/m4-credentials-arm64-20261012.json)保持独立，不覆盖旧结果。

## 第二轮：流程通过与效果边界

所有者明确要求使用修复版再跑一次，继续使用原 $8 总预算。执行冻结的
`model-pilot-prepared-3.json`，不改模型、任务或请求上限，总耗时 556.04 秒。

| 分支 | Agent 退出情况 | 独立判分 | API 请求 | 本轮分支 API 美元 |
| --- | --- | --- | ---: | ---: |
| A-continue | 原生会话正常完成，session ID 保持一致 | solved / 1 | 0 | 订阅成本未知 |
| B-workspace | `InputLimitExceeded` | solved / 1 | 6 | 0.01105530 |
| B-summary | `Submitted` | solved / 1 | 4 | 0.00727665 |
| B-fresh | `InputLimitExceeded` | unsolved / 0 | 3 | 0.00808230 |

B-workspace 在第 7 次请求前达到 40,699 字节，B-fresh 在第 4 次请求前达到 41,695 字节；
均未发送超限请求。它们的运行状态保留为 `failed / agent_limit_or_error`，但工作区、轨迹和
checkpoint 都被保留，独立 verifier 正常完成。统计器按测试结果分别记 solved、unsolved，
不能把 agent 退出方式和任务正确性混为一谈。

13 项检查覆盖：公共前缀封存且不读取隐藏测试、三个继续分支的工作区恢复一致、
Codex session ID 保持、四个独立 verifier、观察记录关联、预约额度和清理。
收尾时启动器取消已封存的源 Trial，最终行显示 `cancelled`；不可变 checkpoint 中保留
`sealed / stage_complete`，这次收尾不表示源阶段失败。

**效果边界：三个继续分支的 tracked Git patch 与前缀完全一致，主要修复已在前缀完成。**
本轮验证了已有工作被保留并能继续执行，尚未证明第二个 agent 补完了一个未完成的修复。
B-fresh 在输入上限前没有形成 tracked patch。这里只有一个独立任务，4 个开发任务计划的
16 条观察中得到 4 条、其余 12 条仍缺失，置信区间不展示；两轮调试不能作为两个独立样本。
不同模型与 scaffold 同时变化，也不能从此推导接力成功率或成本优势。

本轮仅分配 $1.984 调用额度；实际请求预约 $0.806，加首轮 $0.372 共 **$1.178**，
按预约不复用口径，$8 中仍余 **$6.822**。13 次 API 回执全部已结算，首轮账本也保持不变。
Job、Pod、Secret 和资源预约均清理，3 个节点 Ready；47,524 个运行产物文件未发现原凭据值。
完整轨迹与工作区留在忽略的 `work/`，Git 只保存报告、校验摘要和来源索引。

## 首次运行与修复

2026-10-12 使用已冻结的 `sympy__sympy-22914` 计划执行一次。结果均保留：

| 阶段 | 结果 | 原因 |
| --- | --- | --- |
| Codex 公共前缀 | 基础设施失败，未发布 checkpoint | Harbor 的整段 CLI 执行被 RTV 普通命令的 60 秒默认值截断，早于批准的 300 秒阶段时限 |
| B-summary / B-workspace / A-continue | `source_failed`，未启动 | 没有可用的公共前缀 |
| B-fresh | `infra_error`，未判分 | 第 7 次请求的输入达到 40,052 字节，在发送前触发 40,000 字节上限；原处理抛异常，跳过收尾与 verifier |

6 次 API 请求全部有用量回执，预约额 $0.372；没有第 7 次 API 调用。阶段级 UNKNOWN
仍保留，不把 Codex 订阅成本写成零。Job、Pod、运行凭据 Secret 与资源预约均已清理；
33 个运行产物文件未发现本次凭据值。完整轨迹仅保留在忽略的 `work/`。

修复后，无显式超时的 Codex `exec` 使用阶段时限加 30 秒的兜底，外层阶段计时器负责取消；
普通生命周期命令仍限 60 秒，显式工具时限不变。mini 输入超限改为 `InputLimitExceeded`
正常停止，保存现有改动、轨迹并继续独立判分，不截断历史、不提高预算、不补发请求。
运行状态可保留为 agent 达到上限；统计器依据独立 verifier 判定 solved / unsolved。
这是停止策略修复，尚未加入上下文压缩，真实任务仍可能很早耗尽输入额度。

代码通过 189 项单元/模拟测试（1 项真实容器测试需显式启用），Python 3.11 核心测试
108 项通过（52 项可选依赖测试跳过）。零模型真实 Docker 时限探针的 4 项检查通过，
长命令耗时 65.19 秒；修复镜像的 52 个 Python 文件与工作区一致，已导入 3 个节点，
17 项双节点假凭据回归检查通过。Docker 探针命令如下。
以上是重跑前的零模型验证；随后按所有者指示执行第二轮，首轮冻结镜像和失败结果保持不变。

```bash
.venv/bin/python scripts/m4_execution_limits_probe.py \
  --contract work/m4-data/model-repository-package-1/contract.json \
  --output work/m4-control/execution-limits-probe-next
```

## 凭据与执行授权

默认配置没有 `model_access`，仍拒绝真实模型。明确批准后的启动器才创建两个不可变的
Kubernetes Secret，并把 **name + UID 引用**加入该运行的配置。正文不保存 key、登录缓存
或其内容摘要；实际 Secret 值通过 Kubernetes API 直接提交，不经过 shell 参数、共享目录
临时文件或 Workflow 历史。启动器报告只保留经过筛选的错误类型。

- 授权绑定一个 runtime root、冻结 run SHA、5 个 Trial ID、各自 spec SHA 和请求数；
  控制面及 runner 均核对，仅接受 epoch=1，禁止自动恢复和再次执行模型阶段。
- 每个 Job 仅挂载对应 adapter 的一个 Secret，文件 `0400`，只读挂载；不挂载 service-account token。
- 启动子进程前清除继承的模型认证变量，只为当前 adapter 设置 opt-in 和文件路径。
- mini 的 API key 留在外层执行器，工具通过 Harbor 在断网的内层容器执行。
  Codex 由已有 Harbor adapter 将登录缓存复制到内层临时认证目录，checkpoint 只导出允许的文件。
- Secret 创建确认丢失时只读取并验证同名、同内容、同运行的不可变对象，不再次创建。
  Job 启动前复核 Secret UID；清理使用 UID 前置条件，拒绝删除被替换的对象。

两个执行节点在同一 Docker Desktop 宿主上；外层正式 executor 仍使用 privileged DinD，
仅支持可信本机实验。Secret 挂载本身不能按 UID 锁定，启动前检查依赖 namespace 管理者可信；
本实现不声称防御集群管理员、恶意特权进程或恶意 agent 对自身认证文件的读取。
也未实现 Secret 轮换、Codex 刷新后的认证缓存回写、跨宿主 Secret 管理或静态加密配置。
启动器异常退出时，可依据 `credential-intent.json` 和 `credential-references.json` 核对并清理
该运行的 Secret；不得批量删除 namespace 中的其他凭据。

## 冻结试验与费用

两轮均只运行 `sympy__sympy-22914`，沿用原有协议、任务镜像、隐藏测试隔离和四分支顺序。
agent 输入只有公开任务与所选上下文策略，没有脚本修复或参考解。准备命令不接触真实凭据。

| 阶段 | 配置 | 限制 |
| --- | --- | --- |
| 公共前缀 | Codex `0.160.1` / `gpt-6-astra` | 300 秒，完成后 sealed，不运行隐藏验证器 |
| A-continue | 同一 Codex 原生会话 | 300 秒 |
| B-workspace | mini-swe-agent `2.4.6` / `gpt-5.4-mini-2026-03-17` | 300 秒，最多 8 请求 |
| B-summary | 同上，另含未验证摘要 | 300 秒，最多 8 请求 |
| B-fresh | 同上，从原始仓库开始 | 600 秒，最多 16 请求 |

每个 Trial 总期限 1800 秒，包括排队、准备及验证；每池最多一个 Job。
源失败时保留失败，fresh 仍按冻结顺序尝试；基础设施异常不会自动重跑。
真实模型记录与 oracle 记录分别导出。collector 核对实际 spec、上下文策略、模型调用类别、
checkpoint 与前缀账本；未完成验证不能当成成功或失败的已评测结果。
模型没有修好代码也会保留为有效的 unsolved 观察，而不会为了“验收通过”反复调用。

2026-10-12 查询的 [OpenAI 官方价格](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
为每百万 token 输入 $0.75、缓存输入 $0.075、输出 $4.50。
当前 transport 每请求限制 40,000 输入字节与 4,096 输出 token，预约 $0.062，预约不复用；
每轮最多 32 个请求合计 **$1.984**；两轮共享所有者批准的 **$8 API 总预算**，余额保留。
这是本地调用准入边界，不是供应商账单保证。
2 个 Codex 阶段另消耗已有订阅额度；其美元成本保留未知，不标为免费。此轮没有云资源开销。
认证缓存机制参考 [OpenAI 官方认证说明](https://learn.chatgpt.com/docs/auth)，API transport 和
Codex adapter 继续复用 M3 的实现；没有升级模型。

## 使用

先根据[仓库本机指南](m4-repositories.md)准备带固定 Codex CLI 的 agent image/contract，
再由 `cluster prepare-repository` 生成包含该 agent image、独立 verifier 与网络 sidecar 的任务包。
保持本机 PostgreSQL 转发及 Temporal 服务运行。任务包和运行目录必须位于 cluster 共享目录。

```bash
# 零模型、零凭据：冻结待批准的计划；输出与 runtime root 都必须使用新路径。
.venv/bin/python scripts/m4_model_pilot.py prepare \
  --config work/m4-data/model-repository-package-1/config-limits-v1.json \
  --contract work/m4-data/model-repository-package-1/contract.json \
  --root work/m4-data/real-model-pilot-next \
  --output work/m4-control/model-pilot-next.json

# 仅在所有者批准本轮费用之后执行；认证文件为私有普通文件，且在集群共享目录之外。
.venv/bin/python scripts/m4_model_pilot.py run \
  --prepared work/m4-control/model-pilot-next.json \
  --auth-file "$HOME/.codex/auth.json" \
  --api-key-file work/openai-api-key.txt \
  --allow-model-calls --allow-api-calls --approved-budget-usd 2
```

运行会导出 `run.json`、账本及观察记录、检查结果；退出时清理该运行的 Job/Pod/Secret。
后续可以用 `benchmark collect-run` 只读重新导出。失败现场保留在被 Git 忽略的 `work/`，
再次试验须新建计划并核对剩余授权，不能覆写旧结果。

假凭据验收使用 `scripts/m4_credentials_acceptance.py`：在两个节点运行替代 entrypoint，
检查 Secret 投递、权限、adapter 独占变量、无模型客户端导入、日志泄漏与清理。
它不启动正式 supervisor 或模型。首次验收碰到 Kubernetes Python client 将 JSON 日志解码为
Python 字典再转成字符串的问题；改为读取原始日志字节后通过，失败记录保留。

`model-pilot-prepared-3.json` 已执行完毕，不再复用该运行目录。
下一步优先完成其余 3 个开发环境的零模型预检，并设计能观察“前缀尚未修完”的开发任务对照；
如需调整阶段预算或上下文策略，先固定新协议，不能修改已有记录。之后仍需正式评测集、
真实模型故障实验与完整报告。未来付费运行仍须核对剩余授权，不自动启动第三轮。
