# RTV 文档与验收索引

[![中文](assets/lang-zh.svg)](index.md) [![English](assets/lang-en.svg)](index.en.md)

README 展示当前能力；这里保留实现细节与分版本验收记录。测试类型分别标明确定性 fixture、真实容器、假凭据探针和真实模型，开发观察与正式评测分别记录。

| 文档 | 内容 |
| --- | --- |
| [框架开发](framework.md) | 包结构、核心 CLI 与扩展接口 |
| [M0：容器与适配器](m0.md) | 任务格式、容器恢复与能力矩阵 |
| [Codex 原生会话](codex-m0.md) | 真实会话继续与工作区恢复 |
| [M0：恢复边界](m0-boundaries.md) | 取消、回滚与 ATIF 文本导入 |
| [M1：持久执行](m1.md) | 提交、取消、worker 接管与清理 |
| [M2：检查点](m2.md) | 条件提交、执行所有权、费用账本 |
| [M3：交接与分支](m3.md) | 真实双 agent、上下文策略与只读页面 |
| [M4：本机 Kubernetes](m4.md) | 部署、资源准入和脚本故障验收 |
| [任务与统计协议](m4-benchmark.md) | 固定四题开发集、20 题评测集与报告口径 |
| [真实仓库接入](m4-repositories.md) | 任务契约、文件权限与独立验证器 |
| [仓库集群验收](m4-repository-cluster.md) | 任务交付、脚本接力与跨节点恢复 |
| [真实模型 pilot](m4-model-pilot.md) | 凭据投射、自然阶段交接及历史验证 |
| [中途交接协议](m4-tool-prefix.md) | 工具额度、冻结配置与边界检查 |
| [开发记录：第一轮](m4-tool-prefix-results.md) | 四题中途交接与事后诊断 |
| [开发记录：第二轮](m4-early-prefix.md) | 执行环境修正与已有补丁接力 |
| [开发记录：第三轮](m4-runtime-notice.md) | 运行时通知、命令超时与完整判分 |
| [真实 agent 故障恢复](m4-model-recovery.md) | 一次执行丢失、原生会话恢复与资源清理 |
| [正式评测执行](m4-evaluation.md) | 冻结名单、累计预算、断点保护与产物归档 |

使用入口见[演示与部署](demo.md)、[系统设计](design.md)和[路线图](roadmap.md)。筛选后的验收证据位于 [evidence/](evidence/)；完整轨迹、凭据、镜像与大产物不进入 Git。
