# M4：开发集预检与中途交接

[![中文](assets/lang-zh.svg)](m4-tool-prefix.md) [![English](assets/lang-en.svg)](m4-tool-prefix.en.md)

2026-10-12：4 个开发任务的验证环境均已通过；中途交接代码、单元测试及真实
Codex CLI 的断网恢复测试已完成。**尚未用真实模型运行新的工具额度策略，也未完成正式评测。**
本次模型调用为 0，新增 API / 云费用为 $0。

## 开发任务预检

| 任务 | 未修改代码 reward | 标准补丁 reward | 本轮耗时 |
| --- | ---: | ---: | ---: |
| sympy__sympy-22914 | 0 | 1 | 之前已完成 |
| sympy__sympy-15599 | 0 | 1 | 56.12 秒 |
| sympy__sympy-13091 | 0 | 1 | 41.33 秒 |
| scikit-learn__scikit-learn-9288 | 0 | 1 | 119.21 秒 |

后三题使用固定 Harbor 文件与 AMD64 基础镜像；测试容器断网，分别验证 parser、奖励和
退出状态，6 个控制容器已删除。派生镜像、依赖 lock 与原始日志保留在本机 `work/`。
见[后三题证据](evidence/m4-development-preflights-arm64-20261012.json)和
[首题证据](evidence/m4-benchmark-preflight-arm64-20261011.json)。这是环境及验证器验收，
不是 agent 解题结果；未查看 20 个评测任务的运行结果，也未替换样本。

## 中途边界如何工作

新开发协议 [swebench-verified-tool-prefix-v1.json](../configs/benchmarks/swebench-verified-tool-prefix-v1.json)
设定前缀最多准入 **6 次 Bash / apply_patch 工具调用**，仍保留 300 秒总上限。
这是用于开发的初始参数，尚未冻结为最终评测策略。旧自然结束协议及三轮结果不变。

1. 同步 PreToolUse 钩子在文件锁下计数，达到额度后拒绝新的工具调用。
2. 已启动的 shell 可经 write_stdin 完成；PostToolUse 记录匹配的完成收据并提醒结束阶段。
3. agent 正常结束当前会话轮次，留下未经验证的交接摘要。拒绝工具不等于立即停止推理；
   不配合结束而触及总时限仍记失败，不创建前缀 checkpoint。
4. 宿主核对 SessionStart / Stop、会话与轮次身份、工具收据和原生轨迹。缺失钩子、
   重复/未匹配收据、未支持工具、残留进程或版本变化均拒绝封存。
5. 运行器停止整个沙箱，确认容器静止后保存工作区、原生会话、收据及摘要，复用已有
   条件提交与所有权协议。三个继续分支使用同一 checkpoint；前缀额度不继承给继续分支。

自然提前结束（包括零工具）记录 `natural_completion` 并保留，不借助隐藏判分筛选前缀。
达到额度记录 `tool_budget`。工具额度不是 token、API 请求、命令子进程数量或美元上限；
一次 shell 调用可能完成很多工作，6 次调用也可能已解决任务。最终效果必须通过开发实验观察。

前缀、继续及 fresh 的时限仍为 300 / 300 / 600 秒，API 请求上限仍为 8 / 8 / 16。
提前结束的剩余时间不会追加给继续分支。统计时保留实际前缀时间、用量、交接开销与失败；
额度相同不代表实际算力相同。摘要、模型和 scaffold 的混杂因素仍适用。

## 复用与保证范围

复用 [Codex 官方 Hooks](https://learn.chatgpt.com/docs/hooks) 的工具准入与完成通知，
以及 Harbor 0.24.0 的 Codex 适配器；RTV 实现额度协议、收据核对、进程检查及 checkpoint
关联。已直接测试固定的 Codex 0.160.1，不能据此承诺其他版本兼容。

这是可信开发任务中的合作式暂停。钩子本身可能失败后放行，RTV 因此在封存前再次审计；
它不是对恶意 agent 的安全隔离。进程检查覆盖当前容器中新生且仍存活的进程，不覆盖
外部服务副作用，也不保存进程内存。工具不会在任意指令中间强行生成可恢复 checkpoint。
仅沙箱内本仓库上传的固定钩子使用一次性 hook trust 参数，不修改宿主安全设置。

## 验证与复现

218 项单元/模拟测试通过；默认跳过 1 项需显式启用的旧容器测试。Python 3.11 核心测试
131 项通过，55 项可选依赖测试跳过。
并发准入、额度耗尽、提前完成、收据缺失、会话错配、未完成工具、残留进程和协议变更均有检查。

[断网 CLI 证据](evidence/m4-tool-prefix-offline-arm64-20261012.json)使用本地脚本响应代替模型：
实际执行异步 shell 并等待、apply_patch、拒绝第三次工具调用，在沙箱退出后提交及恢复
完整工作区，启动新容器继续原生会话。6 项检查通过，两个容器均已清理。脚本响应不是
真实模型行为，也不是新的 Kubernetes / mini 接力效果验收。
第一次探针的恢复脚本无法覆盖只读 Git 对象，记录保留；改为替换新容器的基线目录后通过。

```bash
.venv/bin/python scripts/m4_tool_prefix_probe.py \
  --image sha256:617df833249959a54991bca0a5128bceb3fa84fb12884361eeaa1e3d6d3a595f \
  --output work/m4-tool-prefix-new
```

该镜像需已在本机；不读取或挂载凭据，不访问真实 provider，输出必须使用新目录。
准备真实开发试验时，先构建并导入包含新代码的 runner，再按原 pilot 指南准备新 pool/config；
不能继续使用旧 runner 来执行新协议。任务与环境完全相同时允许复用原预检和任务契约，
但运行使用新的 protocol SHA、run SHA 和凭据授权。

```bash
.venv/bin/python scripts/m4_model_pilot.py prepare \
  --config <new-runner-pool-config> --contract <prepared-task-contract> \
  --protocol configs/benchmarks/swebench-verified-tool-prefix-v1.json \
  --api-envelope mini-160kb-v1 \
  --root <fresh-runtime-root> --output <fresh-plan-file>
```

下一步是运行新策略的开发对照并检查交接时是否仍有实际工作，然后冻结最终参数再进入
20 题评测。当前累计已知 API 费用仍为 $0.11725620；按不复用预约口径，$20 中余 $14.662。
160 KB 配置每题最多预约 $5.12，4 题共 $20.48；若完整跑这轮开发集，累计预约将达
$25.818，需要先报告增加额度。预约上限不是预期账单或保证发票。此轮未启动付费运行。
