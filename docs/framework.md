# RTV 框架开发指南

[![中文](assets/lang-zh.svg)](framework.md) [![English](assets/lang-en.svg)](framework.en.md)

状态：本地框架与 M0 接入准备 · 2026-10-08

## 1. 当前代码

```text
src/rtv/
  domain.py        # Trial 状态、执行身份、接力上下文与能力声明
  contracts.py     # AgentAdapter / WorkspaceBackend / CheckpointStore / EventSink / Verifier
  checkpoints.py   # 本地 manifest v1、内容摘要引用和格式校验
  storage.py       # 文件检查点与独立工作目录
  events.py        # 单写入者 JSONL 事件
  runner.py        # 组合接口的两阶段本地接力
  demo.py          # 确定性 fixture agent 与 verifier
  harbor_probe.py  # 可选 Harbor 预检查与容器文件恢复原型
  cli.py           # demo / harbor-doctor / harbor-smoke / --version
tests/
  test_domain.py
  test_checkpoints.py
  test_relay.py
  test_harbor_probe.py
examples/tasks/positive-sum/  # Harbor 格式的受控脚本任务
```

基础运行时代码只依赖 Python 标准库，支持 Python 3.11+。可选 `harbor` 组需要 Python 3.12+；Harbor 只在集成入口按需导入。pytest、Ruff 是开发依赖，具体版本锁在 `uv.lock`。`pyproject.toml` 定义可安装的包与 CLI。

## 2. 运行与检查

```bash
uv sync --locked --dev
uv run rtv --version
uv run rtv demo --output work/demo
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
```

`rtv demo` 要求新的输出目录，不覆盖已有实验。CLI 成功退出码为 0；验证失败或运行错误返回非零退出码。演示不会下载任务、调用模型或启动容器。

演示的 A 将 `answer.txt` 写为 41，然后请求交接；B 在另一个执行目录中读取恢复的文件，将其改为 42。独立的 verifier 检查文件内容。这里的验证器独立于 agent 接口，但仍在同一宿主进程中运行，不是隔离的隐藏测试服务。

Harbor 的安装、`harbor-doctor` 和 `harbor-smoke` 使用方法见 [M0 指南](m0.md)。容器原型独立于现有同步 Runner，先验证厂商接口和文件传输，再决定正式 adapter 契约；它不是已完成的 WorkspaceBackend。

## 3. 产物与身份

```text
work/demo/
  events.jsonl
  report.json
  checkpoints/<checkpoint-uuid>/
    manifest.json
    files/
  workspaces/<source-execution-uuid>/
  workspaces/<target-execution-uuid>/
```

事件保留 Trial、Attempt、Execution、epoch 和来源序号。接力保持 Trial ID，创建新的 Attempt / Execution。`ExecutionRef.recover()` 只生成同一 Attempt 的新执行身份与下一 epoch，并未实现租约获取、进程恢复或过期执行拦截。

`report.json` 标记 `mode: local-fixture`，记录来源与目标工作区、checkpoint ID、manifest SHA-256、最终状态和验证结果。

## 4. 本地检查点的保证

- 调用者必须先停止全部写入；fixture 是同步函数，返回后没有后台进程。
- 在临时目录完整写入文件和 manifest 后，用同一文件系统内的目录 rename 发布。
- 只有已发布的 UUID 目录可通过引用加载；读取时验证 manifest 摘要。
- 恢复时验证每个文件的大小和 SHA-256，完整写入后才发布目标目录。
- 保留普通文件内容、文件权限位和空目录；拒绝符号链接、特殊文件、路径穿越和文件 / 目录冲突。
- 拒绝恢复到已有目标；各分支复制文件，不共享可写内容。

本地格式 v1 保存的是文件、摘要和事件边界，尚无原生会话、镜像重建清单或预算账本。它是系统设计中完整 checkpoint 协议的一个实现起点。

存储目录按单进程受控环境使用，不提供对抗恶意磁盘写入的安全边界。源文件并发修改、跨进程提交、断电持久性、引用 GC 和分布式所有权尚不支持。SHA-256 用于完整性检查，不是签名。

## 5. 集成入口

| 接口 | 当前实现 | 下一步 |
| --- | --- | --- |
| AgentAdapter | PrepareAgent / FinishAgent | M0 验证真实 CLI 阶段终止、会话格式与静止能力 |
| WorkspaceBackend | 普通本地目录 | 基于 Harbor 环境接口验证容器及远程工作区契约 |
| CheckpointStore | 本地文件和 manifest | 扩展会话、环境清单、对象存储与条件提交 |
| EventSink | 单写入者 JSONL | 持久身份、去重、重连与查询投影 |
| Verifier | 固定文件内容检查 | Harbor 任务验证器与隔离验证环境 |
| Runner | 同步两阶段函数 | Temporal 编排、取消、恢复和资源清理 |

这些接口是可修改的初始契约，不应在 Harbor 兼容性原型完成前认定为稳定 API。

## 6. 验证范围

测试覆盖恢复 / 交接身份差异、状态迁移、取消后的状态约束、文件与分支隔离、manifest / 内容损坏、缺失文件、非法路径、符号链接、已有目录保护和提交写入失败。端到端测试还验证事件关联、verifier 失败时的最终状态及 CLI 行为。

可选 Harbor 测试使用真实 Task parser 与模拟容器传输，覆盖停止失败、文件往返损坏、验证失败和清理残留。实际容器集成测试默认跳过，需显式设置 `RTV_RUN_DOCKER=1`；不能把模拟测试计为容器验证成功。

当前测试不能证明真实 agent 接力收益、容器隔离、故障后的工作流自动恢复、预算上限或多 worker 正确性。这些仍按[实施路线](roadmap.md)逐步验证。
