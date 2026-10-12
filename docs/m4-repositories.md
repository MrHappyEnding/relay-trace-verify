# M4：真实仓库的接力与恢复接入

[![中文](assets/lang-zh.svg)](m4-repositories.md) [![English](assets/lang-en.svg)](m4-repositories.en.md)

**状态：首个 SWE-bench 开发任务的本机 runtime 接入已通过零模型验收。**
26 项真实容器 / PostgreSQL / Temporal 检查通过；mini-swe-agent 使用脚本响应，
修复动作来自上游参考补丁。这验证执行语义，**不构成模型解题或接力收益证据**。

本次不依赖 GPU。当前 Codex 和 mini 的真实模型入口分别使用订阅服务与 API，推理发生在服务端；
本机负责任务容器、CPU 测试、存储和编排。Lab GPU 可在后续自托管模型或训练路线中使用，
不需要为了使用现有 H100 改变当前目标。若迁往 lab 的 x86_64 Linux 环境，首先受益的是
仓库环境的原生架构、内存和存储，而不必先启用 GPU。

## 新增的运行契约

`rtv benchmark prepare-task` 从[冻结任务协议](m4-benchmark.md)和通过的离线预检生成本机契约。
它绑定 task ID、原始定义摘要、`/testbed`、AMD64 平台、base commit、agent/verifier 镜像 ID、
verifier 超时与显式排除项，计算独立的 `contract_sha256`。
目前只允许 4 个开发任务的契约，实际运行证据覆盖其中 1 个；评测集入口仍关闭。
完整定义必须在创建契约、提交和执行时保持一致，交接及恢复不能更换契约或工作区。

- 提示只读 `instruction.md`；不把 `tests/`、参考解或隐藏奖励加入 agent 上下文。
- 源阶段自然结束后封存，不运行隐藏 verifier，也不要求制造部分错误。只有最终分支独立验证。
- mini 工具 cwd 与 Codex 会话元数据都使用声明目录；Codex 原生恢复检查同版本、同模型、同 cwd。
  新目录下的原生协议校验有单元测试，真实 Codex 会话往返尚待付费/订阅验收。
- 每次独立 verifier 使用冻结的 verifier 镜像，断网并接收声明工作区；它不接收 agent 的原生会话。
  默认 600 秒；除 reward 外还检查 SWE-bench parser 报告，解析失败不能算正常的未解答。
- mini 新入口支持最多 16 请求的 fresh 预算与 600 秒阶段上限；M3 入口继续沿用原有默认值。
  `--ttl` 是整个 Trial 的绝对期限，包含环境和验证；`--agent-seconds` 只约束 agent 阶段。
  这不代表正式实验调度器已经实现了协议的所有排队与统计关联。

## 快照与恢复边界

新仓库契约使用 **manifest v3**：保留常规文件和目录的 `0o777` 权限位、空目录、`.git` 与未提交文件。
v1/v2 仍可读写，旧 fixture 行为不变。符号链接、特殊文件、UID/GID、ACL、xattr 和进程内存仍不支持。
任务若依赖这些内容会被拒绝，不能据此宣称全部 SWE-bench 仓库已兼容。

宿主目录元数据 `.DS_Store` 是唯一允许的排除名称，写入任务契约、manifest 和恢复上下文。
它不参与工作区摘要，也不恢复为任务输入；恢复/验证容器在运行前删除传输过程中夹带的该文件。
不忽略 `.git`、其他隐藏文件或实际代码。快照文件被篡改仍会报错；单元测试覆盖此边界。
保留 Docker 停止屏障，不能把宿主元数据排除推广成允许其他外部进程修改工作区。

分支尚未发布自己的 checkpoint 时，可以从继承的已提交 checkpoint 恢复。
新 epoch / 容器 / 工作区保持原期限，丢弃中断阶段的改动；旧调用留为 UNKNOWN。
实际注入为杀死本轮 A 分支的 supervisor，随后检查恢复并完成验证，不涉及用户的其他进程。

## 零模型复跑

需要现有本机 PostgreSQL、Temporal、Docker 和已安装的 runtime 依赖。
下面使用 M4 PostgreSQL 的本机端口，但以独立 root 和 `rtv3` schema 跑本机 supervisor；
不是 Kubernetes Job 实验。M4 集群已有的 `rtv4` 记录保持独立。先完成[环境预检](m4-benchmark.md)。

```bash
.venv/bin/python -m rtv benchmark prepare-task \
  --upstream work/m4-benchmark/harbor-datasets --task sympy__sympy-22914 \
  --preflight work/m4-benchmark/preflight-new/preflight.json \
  --output work/m4-benchmark/runtime-contract.json

RTV_DATABASE_URL=postgresql://rtv:rtv-local-cluster@127.0.0.1:55434/rtv \
  .venv/bin/python scripts/m4_repository_acceptance.py \
  --contract work/m4-benchmark/runtime-contract.json \
  --output work/m4-benchmark/repository-new
```

脚本串行使用容器额度，创建 1 个源 Trial、3 个继续分支和 1 个 fresh Trial，故障恢复多创建
一个 Execution。它会关闭自身 worker 的模型开关，保留失败尝试并清理自己创建的容器/网络。
参考补丁只用于这个显式脚本控制，不得将其结果导入 `real-model` 报告。

仅安装 Codex CLI 并检查版本，不登录、不调用模型：

```bash
.venv/bin/python scripts/m4_repository_codex_prepare.py \
  --contract work/m4-benchmark/runtime-contract.json \
  --output work/m4-benchmark/codex-prepare-new
```

脚本复用 M0 的官方包 SHA512 校验安装器，版本固定 0.160.1。
`prepare.json` 返回 `agent_image_id`，可传给 `prepare-task --agent-image` 创建另一个契约；
verifier 镜像保持独立。构建与 `codex --version` 通过不代表真实会话恢复通过。

开发者入口 `rtv runtime submit --task-contract ...` 支持 `fixture/mini-fixture/codex/mini`。
脚本 adapter 必须传 `--scripted-controls`，真实 adapter 拒绝该参数。真实 Codex/API 调用需要
提交端及 worker 的双重开关和相应凭据，M4 仍须事先单独批准预算；本次没有读取模型凭据。
不要直接把本机契约交给 Kubernetes runner：它会明确拒绝，避免默用过小的资源配额或缺失任务路径。

## 证据与下一步

[本轮证据](evidence/m4-repository-runtime-arm64-20261011.json)记录所有检查和之前失败尝试。
原始记录在 `work/m4-benchmark/repository-3/`，包括完整 experiment、事件、账本、manifest、
恢复副本和独立 verifier 日志。`.DS_Store` 造成的失败没有从历史删除。
正式统计配置仍使用上一阶段的 SHA，不因这次脚本结果改变选题。

本阶段 API 调用 0、云费用 $0、GPU 使用 0。下一步需要把任务材料、AMD64 镜像、
4 GiB 任务资源及凭据通道接入 Kubernetes，并完成原始运行记录到配对统计的自动关联。
真实双 agent 在这个仓库上的会话交接与效果仍待批准预算后的独立验收；其余 3 个开发任务也尚待环境检查。
