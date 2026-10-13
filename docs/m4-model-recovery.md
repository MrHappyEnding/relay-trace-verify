# M4：真实 agent 的执行器故障恢复

[![中文](assets/lang-zh.svg)](m4-model-recovery.md) [![English](assets/lang-en.svg)](m4-model-recovery.en.md)

本项验收检查真实 Codex 在执行器丢失后，能否从已提交检查点恢复原生会话与代码。
它使用独立的可靠性任务，不进入配对 benchmark 的分母。

## 执行契约

1. 源 Codex 在固定仓库环境中完成一段工作，静止沙箱并提交检查点；该阶段不读取隐藏验证器。
2. 从该检查点创建原生继续分支；确认 `codex exec resume` 进程正在运行。
3. 按 Kubernetes Job UID 删除本次分支的执行器。
4. 控制器最多恢复一次，在另一个执行节点创建新 Job，恢复原生会话与文件。
5. 独立验证器判分，导出执行历史和账本，清理 Job、Pod 及凭据。

最多三个 Codex 订阅阶段：源阶段、被中断的继续阶段、恢复阶段。新增付费 API 请求为 0；
被中断阶段的订阅用量保持 UNKNOWN，不抹去已发生的调用。

`codex-recovery-v1` 凭据权限只覆盖两个预先指定的 trial。源任务只允许第一轮执行；
目标只允许第一轮执行及一次恢复，且恢复必须引用继承的源检查点。普通配对运行依旧
禁止自动模型重放。没有 mini API 凭据、额外任务或无限重试权限。

## 准备与运行

先按 [M4 部署](m4.md)和[仓库交付](m4-repository-cluster.md)准备环境。
使用包含当前源代码的固定 runner 镜像，以及未投射凭据的单 Job 仓库配置。
下面变量分别指向该配置、任务契约和私有 Codex 凭据文件；不要将密钥内容放入命令。

先运行真实集群假凭据检查，不调用模型：

```bash
.venv/bin/python scripts/m4_credentials_acceptance.py \
  --config "$RTV_RECOVERY_CONFIG" --contract "$RTV_RECOVERY_CONTRACT" \
  --output work/m4-data/recovery-credential-check --recovery
```

冻结任务身份、配置、源文件哈希和恢复次数：

```bash
.venv/bin/python scripts/m4_model_recovery.py prepare \
  --config "$RTV_RECOVERY_CONFIG" --contract "$RTV_RECOVERY_CONTRACT" \
  --root work/m4-data/recovery-demo \
  --output work/m4-control/recovery-demo-plan.json
```

在已授权的模型调用范围内执行：

```bash
.venv/bin/python scripts/m4_model_recovery.py execute \
  --prepared work/m4-control/recovery-demo-plan.json \
  --auth-file "$RTV_CODEX_AUTH_FILE" --allow-model-calls
```

运行目录包含 `prepared.json`、`fault.json`、`report.json` 和 `runtime-report/`。
重复运行需要新目录和新计划；既有记录保持不变。

## 验收口径

检查恢复前后文件摘要、会话 ID、检查点身份、attempt 身份、绝对截止时间、执行轮次、
旧执行发布权限和资源清理。verifier 的任务奖励单独记录，不以策略胜负作为恢复验收条件。

Docker Desktop 的 macOS 挂载视图可能把 Git 对象的 `0444` 权限显示为 `0644`。
容器恢复记录与已提交的 Linux 工作区摘要比较；宿主目录摘要只做宿主视图的前后不变检查。
检查点恢复本身仍校验 manifest 中的文件内容、类型和权限。

本地 k3d 的执行节点共享一台物理宿主；本项注入的是执行 Job 丢失。
本项不测量物理宿主断电、任意外部服务副作用回滚或进程内存恢复。

## 验收记录

2026-10-13，针对 `sympy__sympy-22914` 的一次执行器故障注入已在另一个执行节点恢复，
原生会话保持一致，独立验证器通过（`reward=1`）。源检查点包含
`sympy/printing/pycode.py` 及其测试的修改。从开始删除 Job 到恢复工作区耗时
**82.25 秒**，这是单次观测。

[验收证据](evidence/m4-model-recovery-arm64-20261013.json)包含 18 项通过的离线复核和
17 项通过的真实集群假凭据检查。第一版检查器因比较不同文件系统视图，在故障注入前停止。
第二次运行完成了真实恢复和判分；最后一项检查发现检查器自己的 `git diff` 刷新了临时导出
目录的 Git 索引，已提交检查点保持完整。`m4_recovery_audit.py` 重新核验全部检查点文件、
恢复权限、执行记录和原始评分，没有再次调用模型或 verifier。原检查器状态和检查项保留
在证据中。检查命令已改为 `git --no-optional-locks`，并补充回归测试。

两次运行合计五个 Codex 订阅阶段，新增付费 API 请求 0、API 花费 $0；订阅金额未知。
任务 Job、Pod 和凭据均已清理。原定 20 题正式评测仍是独立的待完成项。

可在无需数据库或集群连接的情况下复核保存的产物：

```bash
.venv/bin/python scripts/m4_recovery_audit.py \
  --root work/m4-data/recovery-demo --output work/recovery-audit.json
```

