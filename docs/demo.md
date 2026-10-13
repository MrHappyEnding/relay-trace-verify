# RTV 演示与部署

[![中文](assets/lang-zh.svg)](demo.md) [![English](assets/lang-en.svg)](demo.en.md)

按下面三个层次体验 RTV。每次运行使用新的输出目录；历史运行可以独立查看和复现。

## 1. 核心交接演示

需要 Python 3.11+ 和 uv。无需 Docker、数据库或模型凭据。

```bash
uv sync --locked --dev
uv run rtv demo --output work/demo
```

演示顺序：A 修改文件 → 提交检查点 → B 在独立目录恢复 → 完成修改 → verifier 检查。
查看输出目录的 `report.json` 与 `events.jsonl`，对应工作区和检查点也保留在该目录。
这里使用确定性 fixture，用于演示协议，不计作真实模型结果。

## 2. 持久执行与网页

按照 [M1 服务部署](m1.md)启动 Docker、PostgreSQL、Temporal，然后按
[M3 零费用示例](m3.md#运行零费用示例)启动 worker、提交分阶段任务并创建分支。

```bash
uv run --no-sync rtv runtime serve --port 8765
```

打开 <http://127.0.0.1:8765>，使用中文 / English 按钮切换。页面展示共同检查点、
分支、执行事件、用量与清理状态。viewer 和任务必须使用相同的运行根目录与数据库设置。
页面只读，监听本机回环地址。

## 3. 本机 Kubernetes 与真实 agent

[M4 部署指南](m4.md#部署)提供固定版本的本机 k3d 集群、镜像导入及服务启动步骤。
先运行确定性故障验收，确认执行节点、数据库、Temporal 与资源清理可用。
真实仓库任务还需要[离线验证器预检](m4-benchmark.md)和[任务交付](m4-repository-cluster.md)。

真实模型有两个独立入口：

| 场景 | 入口 | 调用范围 |
| --- | --- | --- |
| Codex 与 mini-swe-agent 交接 | [模型 pilot](m4-model-pilot.md) | Codex 订阅与有预算上限的 API 分支 |
| 运行中执行器丢失后恢复 | [真实故障恢复](m4-model-recovery.md) | 最多三个 Codex 阶段，最多一次恢复，不调用付费 API |

准备命令只冻结配置。执行命令必须显式开启模型调用；凭据从私有文件投射到本次任务，
不写入配置或 Git。订阅调用单列用量，不将 API 花费为零解释为推理免费。

## 验收时看什么

1. 检查点封存后，源工作区和 manifest 是否保持一致。
2. 接手或恢复的执行是否拿到相同文件及约定的上下文。
3. 旧执行是否失去发布权限，恢复是否保留原截止时间和成本记录。
4. 独立 verifier 是否完成，任务结果是否与平台执行状态分别记录。
5. 结束后是否清理任务 Job、Pod、沙箱与本次投射的凭据。

展示时可分别打开检查点、执行历史、恢复记录与最终报告。历史开发记录和正式评测进展
见[文档索引](index.md)与[路线图](roadmap.md)。
