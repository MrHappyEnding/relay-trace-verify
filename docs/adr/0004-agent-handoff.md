# ADR 0004：阶段交接、配对分支与请求记账

[![中文](../assets/lang-zh.svg)](0004-agent-handoff.md) [![English](../assets/lang-en.svg)](0004-agent-handoff.en.md)

状态：已实现；本机真实双 agent 开发任务验收通过 · 2026-10-09

## 问题

M2 可以恢复同一 Attempt，但不能公平比较“原 agent 继续”和“另一个 agent 接力”。
简单复用可写目录会造成分支污染；直接复制摘要会混淆已验证事实和源 agent 的判断；
复制累计 token 又会重复计算共同前缀。

## 决策

1. 在已完成阶段封存源检查点，交接创建新 Attempt，恢复保持原 Attempt。
   接力保留 Trial；实验分叉创建独立 Trial。任务和不可变镜像保持一致。
2. 先持久化准备结果，再由 PostgreSQL 事务激活目标。请求键、上下文 digest、旧身份
   和 epoch 一起约束迁移。prepared 只证明输入准备成功，沙箱创建在激活后进行。
3. 明确分离 native、task-only、summary、portable。跨框架轨迹作为文本资料；不宣称
   将源框架的工具状态导入目标。摘要不额外调用模型，直接使用源最终陈述并标记为自述。
4. 冻结共同 prefix operation IDs；每个分支单独计增量，各策略报告 prefix + increment。
   固定相同任务、镜像和 wall clock 额度；API 请求额度只适用于 mini，不能声称 Codex
   与 mini 有相同的硬 token / 美元上限。暂不提供嵌套分叉。
5. 使用独立、无网络 verifier 沙箱接收工作区；代码哈希与测试 digest 进入结果。
6. 在宿主实现 mini 的 Model / Environment 接口，以便在请求前记 admission、响应后
   记 usage，同时让工具留在 Harbor Docker 沙箱。未知请求禁止重试，不复用其预留。

## 复用、改造与自研

| 来源 | 复用 / 学习范围 | RTV 的适配与限制 |
| --- | --- | --- |
| [mini-swe-agent v2.4.6](https://github.com/SWE-agent/mini-swe-agent/tree/v2.4.6) | DefaultAgent、提示模板、Responses 工具解析 / 观测格式、输出提交协议 | 自定义宿主 Model 和 Harbor Environment；没有复制执行循环，也没有默认启用其本地 shell |
| [Harbor ATIF](https://docs.harborframework.com/agents/atif) | 源轨迹结构与 Codex 转换器 | portable 为有损文本投影；目标原生会话导入没有实现 |
| [Harbor trajectory loading](https://docs.harborframework.com/jobs/loading-trajectories) | 轨迹加载与环境恢复应分开建模 | 检查点 manifest 与独立工作区仍由 RTV 管理 |
| [Temporal Activities](https://docs.temporal.io/activity-definition) | 持久化控制编排与可重试 Activity | 幂等业务键、控制权事务、API 请求不重放与成本归因由 RTV 管理 |
| [OpenAI Responses](https://developers.openai.com/api/reference/resources/responses/methods/create) | API 响应与用量 | 固定模型、输出限制、零 SDK 重试及数据库预留；不是通用账单保证 |

Handoff 状态机、源引用保护、故障收敛、配对前缀归因是本仓库的工程实现；并非声称
首次提出检查点、两阶段准备或 agent 交接。已有机制保留为学习复现内容。

## 后果与证据

本机协议有 48 项零费用检查，另有 14 项真实模型检查通过；真实服务、脚本模型、合成费用与真实用量明确区分，见
[M3](../m3.md)。两种 scaffold 与模型同时改变，且只有一个开发任务；只能用于功能
验收，不能得出框架或接力效果结论。源隐藏测试反馈不传入 B，但 agent 自行执行的测试
观测可能存在于 portable 中，属于策略差异，应在正式实验中冻结并记录。

M3 使用新 schema `rtv3` / 新任务队列，旧 history 不迁移，避免把 M2 Workflow 当作已
验证可回放的 M3 history。产物仍是本地文件；多主机资源 fencing 和 provider 的在途取消
不在保证内。
