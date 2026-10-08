# M0：首个真实 agent 与恢复边界

[![中文](assets/lang-zh.svg)](codex-m0.md) [![English](assets/lang-en.svg)](codex-m0.en.md)

2026-10-08：Codex 的原生会话恢复和新会话交接已在本机 ARM64 Docker 验证。
这是第一轮受控兼容性实验，不是双 agent 效果实验。后续已补齐限定范围的 M0 验收，见 [ATIF 与中断回滚](m0-boundaries.md)；以下证据保留首轮实验范围。

## 固定配置与归属

| 部分 | 本次配置 / 负责方 |
| --- | --- |
| Agent | 官方 Codex CLI 0.160.1，模型 `gpt-6-astra`，reasoning effort `low` |
| 模型访问 | 现有 ChatGPT 订阅登录；不启用按量付费 API |
| Harbor | 0.24.0；复用 Codex adapter、DockerEnvironment、Task、Verifier |
| 容器 | Linux/ARM64，Engine 28.5.1，Compose 2.40.0-desktop.1；1 CPU / 1 GiB |
| RTV | 检查点打包与完整性校验、恢复 / 交接身份、静止边界、协议断言和用量归因 |

基础 Python 镜像固定 digest；Codex 官方 npm 平台包固定版本和 SHA-512，直接安装原生
可执行文件，不运行远程安装脚本。安装器也固定了 x64 包，但本次只验证 ARM64。
恢复分支使用源容器的确切 image ID。Harbor 网络 sidecar 基础镜像尚未固定 digest。

## 运行方法

在仓库根目录执行；每次选择新的输出目录：

```bash
uv sync --python 3.13 --locked --group harbor --dev
# 构建并检查容器，随后清理；不读取登录信息、不调用模型。
uv run --no-sync rtv codex-prepare --output work/codex-prepare-1

# 消耗现有订阅额度。先确认此登录所属账号及可用额度。
uv run --no-sync rtv codex-smoke \
  --auth-file "$HOME/.codex/auth.json" \
  --model gpt-6-astra --allow-model-calls \
  --turn-timeout 240 --output work/codex-smoke-1
```

`--allow-model-calls` 必须显式提供。当前仅接受包含 ChatGPT tokens 且不含 API key
的登录缓存；这不是通用 API / lab 推理服务适配器。登录过期或账号不支持模型时，
报告失败并清理容器，不自动换模型或重试。开启新付费服务仍遵守[费用约定](learning-plan.md)。

凭据临时复制到容器内，Harbor 在执行后删除，最终整个容器也会删除。只挂载本次
独立的 agent / verifier 日志目录，不挂载用户主目录或 Docker socket。原始会话、
提示、代码和事件均保存在忽略的 `work/` 下，不应公开上传。agent 出网限于
`chatgpt.com`、`auth.openai.com`、`api.openai.com`；verifier 阶段切换为 `no-network`。

## 三次执行验证什么

1. **源阶段**：真实 Codex 实现正数过滤，按受控提示保留空输入缺陷；记住一个随机
   标记但不写入工作区。独立 verifier 判失败。
2. **检查点**：额外启动受控后台写入进程，确认仍在写；停止整个源容器并核对状态，
   两次导出的文件摘要必须一致。把 `/workspace`、单个原生 rollout 和版本 / session
   元数据放入同一个检查点，所有文件受 manifest 哈希保护。凭据不在快照范围内。
3. **原生恢复**：新容器恢复工作区并导入原生会话；保留 Trial / Attempt，产生新的
   Execution 和 epoch。必须保持 session ID，并从会话历史取回未出现在当前提示或
   工作区的标记，最后修复任务并通过测试。
4. **语义交接**：另一个新容器只接收相同工作区和结构化交接包，不导入原生会话。
   保留 Trial、产生新 Attempt / Execution / session ID；也必须完成任务并通过测试。
5. **隔离与清理**：恢复前文件摘要与源相同；分支修改不污染源；本次全部容器被删除。

这里的两个接收分支使用**同一种 CLI 和同一个模型**。交接包由测试程序构造，
没有验证自动摘要的质量。第二种 agent 留给后续 M3 接力实验。

Harbor 的 `load()` 使用 `resume --last`；RTV 在全新容器的独立会话目录中只允许
导入一个 rollout，并核对返回的 session ID，避免“last”选到其他用户会话。
缺少会话、版本 / 模型不符、内容损坏或来源未停止时，不静默降级为新会话。

## 证据与用量

精简报告：[2026-10-08 实验](evidence/m0-codex-arm64-20261008.json)。原始产物位于
`work/codex-smoke-20261008-2/`。三次执行完成，17 项断言通过，总耗时约 105 秒，
两个接收分支 reward 均为 1，容器清理错误为零。

Codex 0.160.1 的 `turn.completed.usage` 在恢复后包含**会话累计用量**。RTV 保留
原始 `session_usage`，扣除导入前的累计值后得到增量 `usage`；计数缺失、回退或
字段不兼容时记为未知，不能当作零。完整通过的一轮为 134,682 输入 token
（其中 115,200 为缓存输入）和 1,200 输出 token。输入包括每次模型请求重复送入的
上下文，不能理解成用户新输入的字数；缓存量是输入的子集。

开发中第一次运行在源 agent 完成后，因 RTV 未处理容器命令的 `stdout=None` 失败；
已增加回归测试。计入该次开销，本次共 4 个完成的 agent turn，182,758 输入 token
（150,528 缓存输入）、1,569 输出 token。一个 agent turn 可包含多个模型请求。
使用现有订阅，没有启用额外 API 计费；美元成本仍为 `null`，不编造订阅 token 单价。
提交的报告离线重新解析了保留日志以纠正累计口径，未改写原始执行报告或重新调用模型。

## 恢复保证与未验证范围

| 情况 | 当前行为 / 边界 |
| --- | --- |
| 阶段正常完成 | 验证 completed 事件和唯一 rollout；不能只信 shell 管道退出码 |
| 源容器停止 | 包括受控后台写入进程在内的容器进程全部终止，再导出文件 |
| 超时 / 取消 | 停止沙箱；不提交该未完成阶段；清理并报告。协议故障注入使用模拟测试 |
| 原生恢复 | 固定 CLI / 模型、相同镜像和 `/workspace` 路径；仅恢复文件和会话历史 |
| 损坏 / 错误版本 / 多个 session | 拒绝继续；不自动重跑或丢弃会话 |
| 预算 | 限三个 agent turn 和每次 wall clock；不能拦截每个内部请求，金额 / token 准入仍是软边界 |
| Worker 崩溃 | 尚无持久编排、租约或 epoch fencing；本地 finally 清理不等于崩溃后的清理服务 |
| 进程、ATIF、第二种 agent | 不恢复进程内存或未决工具调用；ATIF 导入、跨模型和跨框架接力未验证 |

只支持可信、可重建的文件系统任务；verifier 在目标容器内运行，尚不是隔离隐藏测试
服务。阶段结束后导出成功不代表任意工具执行到一半都能恢复。

后续 [M0 验收](m0-boundaries.md)已完成同模型 ATIF、真实取消 / 回滚和能力声明，并修复了镜像被清理的问题。当前下一步是 M1 持久执行；第二种 agent 留给 M3。

## 参考

- [Codex 非交互执行与 resume](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Codex 认证及容器登录缓存](https://learn.chatgpt.com/docs/auth)
- [Harbor 轨迹加载](https://docs.harborframework.com/jobs/loading-trajectories)

原生会话导入复用 Harbor / Codex 已有能力；RTV 的工作是围绕它们建立可验证的
文件一致性、身份和失败边界，不能把上游实现计为本项目独创。
