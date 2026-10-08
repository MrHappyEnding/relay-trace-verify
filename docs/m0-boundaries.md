# M0 验收：ATIF、中断与回滚

[![中文](assets/lang-zh.svg)](m0-boundaries.md) [![English](assets/lang-en.svg)](m0-boundaries.en.md)

2026-10-08：**首个 Codex 适配器的 M0 限定范围验收完成**。在已验证的原生恢复和
语义交接基础上，补齐同模型 ATIF 导入、真实工具运行中的取消与检查点回滚、镜像
生命周期和能力声明。M1–M4 尚未实现；这不是 worker 崩溃后的自动恢复系统。

## 复现

需要 [Codex M0 指南](codex-m0.md)中的 Docker、Harbor 和 ChatGPT 登录。以下执行
一次源阶段，再执行 ATIF 分支、主动中断分支和回滚恢复分支，消耗现有订阅额度：

```bash
uv sync --python 3.13 --locked --group harbor --dev
uv run --no-sync rtv capabilities
uv run --no-sync rtv codex-prepare --output work/prepare-new

uv run --no-sync rtv codex-smoke --checkpoint-only \
  --auth-file "$HOME/.codex/auth.json" --allow-model-calls \
  --output work/checkpoint-new
uv run --no-sync rtv codex-boundaries \
  --source-run work/checkpoint-new \
  --auth-file "$HOME/.codex/auth.json" --allow-model-calls \
  --output work/boundaries-new
```

每次使用新输出目录。不带 `--checkpoint-only` 的 `codex-smoke` 仍执行原来的三个
阶段；它生成的成功报告也能作为 `--source-run`。模型默认 `gpt-6-astra`，CLI 固定
0.160.1，Harbor 固定 0.24.0；两次命令的模型必须一致，不接受 API key 或自定义端点。

只检查转换、不调用模型：

```bash
uv run --no-sync rtv codex-atif-audit \
  --native "work/checkpoint-new/bundle/native/rollout-<实际文件名>.jsonl" \
  --output work/atif-audit-new
```

上面的 rollout 路径需替换为本次真实文件名；CLI 不负责猜测多个历史会话中的“最后一个”。

## ATIF 保留与丢失

复用 Harbor 的原生 → ATIF → 原生转换和 `load()`。RTV 将接收端元数据中的默认
`/app` 改为 `/workspace`、设置接收端 CLI 版本，并分配新 session ID；来源 session
写入 ATIF lineage。**ATIF 导入属于语义分支，不代表无损原生恢复。**

实际源轨迹包含 7 条消息、3 次工具调用、3 个结果，转换后全部通过文本 / JSON
归一化比较。消息文本块拼接、工具输出文本块以换行拼接，因此不声称字节一致。
真实新会话从导入历史中找回未出现在新提示或工作区的随机标记，并完成任务、通过验证。

| 内容 | 已验证范围 |
| --- | --- |
| 文本消息、工具参数和文本结果 | 本例内容保留；调用与结果必须一一配对 |
| 工作区 | 单独从文件检查点恢复，ATIF 不包含文件系统 |
| 元数据 / 文本块边界 | 部分丢失或规范化；完整差异计入 conversion audit |
| 原生 base instructions、world state、turn context | 不保证保留；接收端运行配置重新建立 |
| 推理字段、原生累计用量 | 不重建为原生状态；来源成本必须单独归因 |
| 图片、分段 / 嵌套轨迹、其他 agent / 模型 | 当前适配器拒绝未支持输入，跨框架能力未验证 |

转换器可能估算 API 美元价格，本实验会清除这些估算，避免冒充订阅账单。ATIF
新会话的原生计数从新请求开始，来源阶段用量保留在来源报告，不能因此把前缀成本清零。

## 真实中断协议

1. 从已提交检查点恢复原生会话和工作区，Execution 的 epoch 从 1 变为 2。
2. 真实 agent 调用受控 Python 工具。工具创建独立进程会话中的子进程，持续修改
   `uncommitted.txt`；父工具等待，不完成任务。
3. 控制器验证父 / 子 PID 均存活、文件确实变化，然后取消本次执行。
4. RTV **先停止整个沙箱并核对容器 PID 为 0，再取消并回收本地 transport 任务**。
   仅取消 `docker exec` 客户端不能证明远端工具已停止。
5. 缺少 completed 事件的轨迹被拒绝；不发布新检查点。中断后的文件和会话只留作
   诊断产物，不成为恢复来源。来源检查点目录摘要保持不变。
6. 新 Execution 使用 epoch 3，从原先提交的检查点恢复。未提交文件不存在；原生
   session ID 保持，历史标记可找回，任务最终通过验证。

这是**工具运行中取消后，回滚至上次完成阶段再执行**；不恢复任意进程内存，也不
继续那个未完成工具。真实实验覆盖主动取消；超时、停止失败、transport 错误的顺序
由故障注入单测验证。硬杀 worker、租约和旧 epoch 拦截留给 M1/M2。

## 镜像与快照契约

实验发现 Harbor `stop(delete=True)` 的 `down --rmi local` 会删除自动构建的源镜像，
即使额外加标签也不足以保留。RTV 对当前无持久 Docker volume 的可信 fixture 使用
`stop(delete=False)` 执行 Compose down，删除容器和网络，保留镜像产物。
报告中的 `rtv-checkpoint-cache:<image-id>` 是明确保留的引用，恢复前检查确切 image ID
仍可用。缺失则在模型执行前失败；不静默重建一个不同镜像。

镜像缓存是检查点依赖，不计为容器泄漏；不使用全局 prune。当前只有本机保留，
尚无镜像仓库发布、引用 GC 或机器丢失后的恢复保证。旧版报告的镜像可能已被清理，
这种情况下需创建新检查点。成功运行报告的本地 source-run 元数据也是恢复所需产物。

`rtv capabilities` 输出的快照契约包含 `/workspace`、一个完整原生 rollout 和会话
元数据。保留普通文件字节、权限及空目录；拒绝符号链接、特殊文件和路径穿越。
目录权限按恢复进程默认值建立，不保留 UID/GID、ACL、xattr、进程或外部服务状态。
必需生成物要位于 `/workspace`；登录缓存和临时工具进程不进入快照。

## 本次证据与资源

- [真实实验报告](evidence/m0-boundaries-arm64-20261008.json)：23 项断言全部通过，
  ATIF 和回滚分支 reward 均为 1，清理错误为零，清理后原镜像仍可用。
- 原始产物：`work/codex-checkpoint-20261008-2/` 和
  `work/codex-boundaries-20261008-3/`；敏感原始轨迹只保留在忽略的 `work/`。
- 镜像保留问题导致一次额外源阶段执行；两个环境失败尝试没有调用模型。
  开发开销计入精简报告，没有隐藏重试成本。
- 中断调用最终 usage 为 `null` / UNKNOWN；观测到的 session 累计计数是下界且包含
  导入前缀，不能直接加进总量。已完成调用的小计和未知部分分开保存。
- 使用现有 ChatGPT 订阅，无额外付费 API；美元成本不估造。wall clock 终止还包含
  停止 / 清理等待窗口，不提供严格 token 或金额准入。

下一步进入 **M1：单 agent 持久执行**，先落实稳定 operation ID、Temporal 编排、
状态查询和取消，再验证 worker 重启后的沙箱核对。第二种 agent 仍安排在 M3。

参考：[Harbor 轨迹加载](https://docs.harborframework.com/jobs/loading-trajectories)、
[Codex 非交互执行](https://learn.chatgpt.com/docs/non-interactive-mode)。转换与原生
恢复复用上游；RTV 负责工作区、环境、身份、终止次序和验证边界。
