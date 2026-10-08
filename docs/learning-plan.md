# RTV 学习、复现与资源计划

[![中文](assets/lang-zh.svg)](learning-plan.md) [![English](assets/lang-en.svg)](learning-plan.en.md)

更新：2026-10-08

## 项目目的

RTV 同时服务于学习复现、系统实现和自主探索。已有方案是学习材料和对照基线，
不是删除功能的理由。每项工作分别记录参考来源、复现范围、修改之处和验证证据。
工程深度与研究新颖性分别评价；运行模拟样例不能证明真实模型效果。

## 参考与复现清单

以下是阅读与复现计划。除 M0 中单独列出的 Harbor 检查外，目前没有完成这些项目的实验复现。

| 参考 | 学习或复现目标 | 对应 RTV |
| --- | --- | --- |
| [Harbor](https://docs.harborframework.com/jobs/loading-trajectories) | 任务 / 环境 / verifier 接入；分别验证原生会话、ATIF 与文件恢复 | M0、M3 |
| [Temporal Activities](https://docs.temporal.io/activity-definition) | 复现执行完成但回执丢失后的重试，验证操作幂等与核对 | M1、M2 |
| [LangGraph time travel](https://docs.langchain.com/oss/python/langgraph/use-time-travel) | 从图检查点派生分支，理解图状态和外部环境的边界 | M3 的对照实现 |
| [Handoff Debt 论文](https://arxiv.org/abs/2606.02875) / [代码](https://github.com/anjilab/agent-handoff-debt) | 固定仓库状态，小规模复现不同交接信息的效果；保留原 agent 继续基线 | M3、M4 |
| [Crab 论文](https://arxiv.org/abs/2604.28138) / [代码](https://github.com/open-agent-infra/crab) | 阅读、运行文件和进程恢复示例，明确恢复范围及外部副作用限制 | 恢复专题；当前 M2 不承诺进程内存恢复 |
| [AgentFS](https://github.com/tursodatabase/agentfs) | 理解文件、状态、工具记录的存储和快照；与完整目录快照比较 | 存储专题 |
| [OpenHands SDK](https://github.com/OpenHands/software-agent-sdk) | 会话生命周期、持久化与 workspace 接口 | Adapter 参考 |
| [AgentRx 论文](https://arxiv.org/abs/2602.02475) / [代码](https://github.com/microsoft/AgentRx) | 约束检查、证据日志、失败步骤定位及标注校准 | Trace 后续扩展 |
| [DeltaBox](https://arxiv.org/abs/2605.22781) | 学习文件与进程增量检查点，评估是否值得深入性能复现 | 后续系统专题 |
| [Ember](https://github.com/RuokeZhang/ember) | 工程范围、系统边界和验证方式参考 | 项目深度参照 |

最初讨论中的 AgentScaling / AgentTracer 来自项目所有者提供的描述；尚无核对过的
原始代码或论文入口。它们作为选题来源保留，不写成已验证的性能依据。

## 资源与费用约定

- 所有者确认可使用 lab 的 4 张 H100。显存规格、主机架构、SSH、调度器、网络、
  存储额度及使用规则尚待确认；目前没有连接或启动任何 lab 作业。
- 模型服务与任务沙箱可以分别部署。开发和系统故障测试优先使用确定性 fixture；
  自托管模型是后续可选实验后端，不能假定任意 agent CLI 都兼容它。
- 任何新增付费 API、云资源或其他支出，先说明用途、预计金额、运行规模和停止条件，
  得到所有者确认后执行。预算估计不是花费授权。
- 本次 M0 准备工作不调用模型、不创建付费云资源。下载公开依赖和基础镜像仍消耗网络与本地磁盘。
- 之前估计的完整 M0–M4 工作量约 400–650 小时属于规划假设，不是承诺；
  lab 自托管后的现金预算需在小规模实测后重算。不要沿用商业 API 单价推算 lab 现金支出。

## 复现实验的交付标准

每个复现记录固定依赖和配置、原论文或项目版本、自己的命令与样本、原始产物引用、
结果差异和失败案例。小样本复现、完整论文复现、功能移植和自主扩展分别标注。
论文报告的数字始终归属于原作者；只有实际测得的结果才能标为 RTV 结果。
