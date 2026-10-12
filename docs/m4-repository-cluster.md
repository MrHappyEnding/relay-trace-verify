# M4：Kubernetes 真实仓库执行与统计关联

[![中文](assets/lang-zh.svg)](m4-repository-cluster.md) [![English](assets/lang-en.svg)](m4-repository-cluster.en.md)

**状态：28 项真实集群脚本控制检查通过，M4 真实模型实验仍未完成。**
[验收证据](evidence/m4-repository-cluster-arm64-20261012.json)包含原始产物摘要及失败历史。

本阶段将 `sympy__sympy-22914` 接入本机 k3d，使用真实 Job、Harbor 容器、PostgreSQL、
Temporal 与 mini-swe-agent 执行循环。修复动作来自脚本和上游参考补丁；没有模型调用，
不能用这些结果推断 agent 的成功率或接力收益。其余三个开发任务和正式评测集仍未运行。

## 任务交付与资源

`cluster prepare-repository` 在共享目录创建独立的任务包。原始定义不变，交付后重新计算
路径绑定的契约摘要；验证 task digest、不可变 agent/verifier 镜像 ID 与 AMD64 平台。
Docker 镜像归档另有 SHA256，每个 Job 加载前校验，加载后再次检查仓库镜像身份。
任务契约不包含 API key。默认池仍拒绝真实模型；后续 opt-in 通道见[模型凭据与 pilot](m4-model-pilot.md)。

| 项目 | 仓库执行池 | 原有小型 fixture 池 |
| --- | --- | --- |
| 同时执行的 Job | 1 | 2 |
| 每个 Job 的 CPU / 内存 | 2 CPU / 5120 MiB | 1 CPU / 1536 MiB |
| Docker engine 临时目录上限 | 16 GiB | 4 GiB |
| Pod 临时存储 request / limit | 17 GiB | 5 GiB |
| 镜像加载超时 | 300 秒 | 90 秒 |
| 容器启动超时 | 180 秒 | 60 秒 |

仓库任务本身声明 1 CPU、4 GiB 内存和 10 GiB 存储，外层为 Docker daemon 与执行器预留余量。
本机 namespace 内存配额提升至 6144 MiB，CPU 配额仍为 2500m，包含 PostgreSQL。
配置固定到独立 runtime root；不要复用不同 runner 镜像或资源配置的旧 pool。
临时存储限额由 kubelet 监控，不能将其描述成同步文件系统硬配额。

外层执行器使用宿主原生 ARM64 镜像，内层任务声明 `linux/amd64`。这依赖当前 Docker Desktop
提供的异构执行能力，不代表任意 ARM Kubernetes 集群都能直接运行该镜像。未启动 GPU 任务。
沿用[ADR 0005](adr/0005-kubernetes-executions.md)的每 Job 独立 Docker daemon 设计，
复用 Harbor 环境及上游 SWE-bench verifier；RTV 负责身份、契约、恢复边界和统计关联。

## 从预先分配到观察记录

`benchmark.execution.prepare_pair` 在提交任何 Trial 前生成 `run.json`，绑定原协议 SHA、
任务 / repeat、分支顺序、Trial ID、上下文策略和执行 spec。本页证据仅覆盖 pilot 的 oracle 控制；后续启动器另支持显式的 real-model 模式。
源阶段为 300 秒，继续分支为 300 秒，fresh 为 600 秒；整个 Trial 的 1800 秒期限还包含
镜像准备和验证。实验分支禁止自动恢复；额外的故障 Trial 独立运行，不替换实验样本。

`collect-run` 从数据库快照推导结果，校验 spec、源 checkpoint、prefix operation ID 与实际
handoff 策略；导出原始记录 SHA256、排队 / 总耗时、用量账本、JSONL、CSV 和中英文报告。
源 Trial 清理或到期后，仍通过已提交的 sealed checkpoint 验证历史关联。

- reward=0 但 verifier 未完成，归类为基础设施错误，不能当成已评测的未解决任务。
- 缺失分支仍留在原计划的分母；本次只覆盖 4/16 个 pilot 观察，另外 12 个保持 missing。
- UNKNOWN 成本保留 null 和已知费用下界；不将订阅推理标为免费。
- 真实模型调用记录不能导入 oracle 报告；有缺失观察时不展示置信区间。
- 本轮验收脚本要求控制实验成功，失败即保留现场并清理。它不是完整的正式实验调度服务。

## 复跑与导出

先完成[仓库本机接入](m4-repositories.md)及[本机集群部署](m4.md)，保持 PostgreSQL 转发与
Temporal 服务运行。使用新的输出目录，旧的失败和成功产物都保留在 `work/`。

```bash
.venv/bin/python scripts/m4_local_setup.py --build

.venv/bin/python -m rtv cluster \
  --config work/m4-control/config.json --root work/m4-data/preparation \
  prepare-repository --contract work/m4-benchmark/runtime-contract.json \
  --output work/m4-data/repository-package-new

.venv/bin/python scripts/m4_repository_cluster_acceptance.py \
  --config work/m4-data/repository-package-new/config.json \
  --contract work/m4-data/repository-package-new/contract.json \
  --output work/m4-data/repository-cluster-new
```

验收先执行源阶段及冻结顺序的四个分支，然后单独注入 Job 删除故障。清理仅针对本轮 pool
的 Job UID；确认 Job 和 Pod 都消失后才释放预约。恢复从继承的 checkpoint 创建新 epoch，
避开故障节点，保留原始 deadline 与 UNKNOWN 调用。

只重新导出数据库观察，不启动任务或模型：

```bash
RTV_SCHEMA=rtv4 RTV_RUNTIME_ROOT="$PWD/work/m4-data/repository-cluster-new" \
RTV_DATABASE_URL=postgresql://rtv:rtv-local-cluster@127.0.0.1:55434/rtv \
  .venv/bin/python -m rtv benchmark collect-run \
  --run work/m4-data/repository-cluster-new/run.json \
  --output work/m4-data/repository-cluster-new/observations-new
```

## 验收边界与已知差异

Docker Desktop 的共享目录中，本次观察到 4 个 Git 只读文件在 Linux 视图为 `0444`，
macOS 宿主视图为 `0644`。因此验收分别比较同一视图的摘要：宿主源目录与恢复目录，
以及容器内 `workspace.restored` 事件与源运行摘要。manifest 仍保留原始权限；运行时
恢复与独立 verifier 仍逐次校验内容、路径和权限，没有忽略 Git 元数据。
第一次失败是验收脚本直接跨视图比较摘要，失败记录保留。

后续一次恢复已完成修复及测试，但共享临时校验目录在清理时出现新的 `.DS_Store`，
导致发布前失败。checkpoint、恢复和交接的临时校验现已使用进程私有目录；持久化产物
仍在共享存储。另一次在并行本机回归期间触发原有 60 秒容器启动超时，仓库启动现有
独立的 180 秒上限，工具超时和 Trial 总期限仍分别受限。本机重型验收应串行执行。

本机两个节点共享同一 Docker Desktop 宿主和共享目录；Job 删除与换节点恢复不能证明
物理宿主丢失后的高可用。外层 Pod 仍为 privileged，只支持可信本机开发控制。
后续[模型凭据通道](m4-model-pilot.md)已通过假凭据测试；Codex 原生仓库会话、真实双 agent 效果与费用实验仍待完成；
新增付费调用须先核价并批准。当前阶段 API 调用 0、云费用 $0、GPU 使用 0。
