# M4：模型凭据通道与首个真实仓库试验

[![中文](assets/lang-zh.svg)](m4-model-pilot.md) [![English](assets/lang-en.svg)](m4-model-pilot.en.md)

**状态：凭据通道及真实模型 pilot 启动器已实现，17 项本机 Kubernetes 假凭据检查通过；
真实模型运行尚待本次费用批准。** 没有读取或上传真实凭据，没有调用模型，也没有使用 GPU。
[阶段证据](evidence/m4-credentials-arm64-20261012.json)区分模拟测试、真实集群假凭据测试和待执行项。
这不表示真实登录、Codex `/testbed` 会话继续或 SWE-bench 模型效果已验证。

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

第一轮仅运行 `sympy__sympy-22914`，沿用原有协议、任务镜像、隐藏测试隔离和四分支顺序。
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
32 个请求合计 **$1.984**，建议本轮批准 **$2 API 预算**。这是本地调用准入边界，不是供应商账单保证。
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
  --config work/m4-data/model-repository-package-1/config.json \
  --contract work/m4-data/model-repository-package-1/contract.json \
  --root work/m4-data/real-model-pilot-1 \
  --output work/m4-control/model-pilot-prepared-1.json

# 仅在所有者批准本轮费用之后执行；认证文件为私有普通文件，且在集群共享目录之外。
.venv/bin/python scripts/m4_model_pilot.py run \
  --prepared work/m4-control/model-pilot-prepared-1.json \
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

剩余工作：本次真实会话/交接验收、其余 3 个开发环境、正式评测集、完整报告与展示。
