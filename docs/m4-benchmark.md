# M4：真实仓库任务与配对统计

[![中文](assets/lang-zh.svg)](m4-benchmark.md) [![English](assets/lang-en.svg)](m4-benchmark.en.md)

**状态：任务选择、统计协议、离线分析工具已实现；全部 4 个开发任务的真实容器验证器预检通过。**
后续[集群仓库阶段](m4-repository-cluster.md)已完成脚本接力与恢复；[修复版真实模型试验](m4-model-pilot.md)已完成一个任务的四分支判分，尚无正式评测结论。
本次新增 API 调用为 0、云资源费用为 $0。完整 M4 仍进行中。

## 来源与冻结

任务来自 [SWE-bench Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified)，
复用 [Harbor SWE-bench adapter](https://github.com/harbor-framework/harbor/blob/main/adapters/swebench/README.md)
生成的任务、镜像、测试与参考解。RTV 自己实现任务选择、配对身份、失败保留、费用归因和统计报告；
不把上游题目或验证能力计为原创。

| 来源 | 固定身份 |
| --- | --- |
| Verified 数据 | `c104f840cc67f8b6eec6f759ebc8b2693d585d4a`，完整 500 条元数据 |
| Harbor tasks | `laude-institute/harbor-datasets`，`86723674f04e4209ac479d0fb75d9d9f44b4377e` |
| Harbor runtime | `0.24.0` |
| 任务环境 | 24 个 `linux/amd64` 镜像分别按 manifest digest 固定 |
| 协议 SHA256 | `dbd68d56849681a5ac991409743d0276ee8b5572992c3570a18ae532ab4cd8bb` |

[冻结配置](../configs/benchmarks/swebench-verified-v1.json)包含基线提交、镜像身份和 144 个上游文件的 SHA256。
[完整目录](../configs/benchmarks/swebench-verified-catalog.json)仅包含任务 ID、仓库、基线提交及来源身份。
下载的题目、参考解、隐藏测试和完整运行产物留在忽略的 `work/` 中。

按 `sha256("rtv-m4-swe-v1:" + instance_id)` 排序，前 4 个作开发集，随后 20 个作评测集，
选择发生在观察结果之前。开发集为 `sympy__sympy-22914`、`sympy__sympy-15599`、
`sympy__sympy-13091`、`scikit-learn__scikit-learn-9288`。两组互斥；不按是否容易、能运行或
最终分数替换任务。环境不支持时保留失败记录。修改协议须发布新身份，不能覆盖旧配置。

新增开发阶段的[工具额度交接协议](m4-tool-prefix.md)具有独立身份，复用完全相同的任务与环境预检。下文保留历史自然结束协议的定义。

## 实验协议

每个任务的每次重复是一个配对块。A 在 300 秒上限内完成一个自然结束的阶段后提交 checkpoint；
不依赖隐藏测试奖励决定是否分叉。源阶段超时则三个继续分支记 `source_failed`，保留已发生成本；
从头开始的分支仍可独立运行。摘要取源 agent 的最终消息并标为未经验证，不额外调用模型。

| 分支 | 初始状态与上下文 | 执行上限 |
| --- | --- | --- |
| A-continue | 同一 checkpoint + A 原生会话 | 300 秒 |
| B-workspace | 同一 checkpoint + 原始任务 | 300 秒 / 8 个 API 请求 |
| B-summary | 同一 checkpoint + 原始任务 + 未验证摘要 | 300 秒 / 8 个 API 请求 |
| B-fresh | 原始仓库与任务，没有 checkpoint 或前缀成本 | 600 秒 / 16 个 API 请求 |

开发集重复 1 次：4 个前缀、16 个分支。评测集重复 2 次：40 个前缀、160 个分支。
块内执行顺序由稳定哈希预先确定，分支拥有独立工作区。排队时间另报，不消耗执行时间上限。
时间上限是准入约束，不代表各分支实际消耗相同；报告必须保留实际时间和用量。

主要比较为 **B-summary − A-continue**；其余比较是探索性的描述结果。
A 计划为 Codex CLI 0.160.1 / `gpt-6-astra`，B 为 mini-swe-agent 2.4.6 /
`gpt-5.4-mini-2026-03-17`。模型和 scaffold 同时变化，不能把差异解释成单独的交接协议效应。
B-summary 与 B-workspace 才更直接对应摘要的增量作用。B-fresh 比较整条策略，不是共同 checkpoint 的继续。
这些是待接入的运行配置，分析 CLI 不会启动模型。

## 统计与费用口径

- 分母使用全部计划观测。基础设施失败、取消和源阶段失败在 ITT 成功率中计为未成功，
  同时单列原因与真正完成验证的分母；不悄悄过滤。
- 缺失行显式补为 `missing`，相关比较为暂定结果，不展示置信区间；完全缺失时差值为 `null`。
- 先计算每个任务跨重复的平均配对差，再按**任务**聚类 bootstrap 10,000 次，种子 20261011，
  输出百分位 95% 区间。重复和分支不是独立样本。20 个任务仅供探索，不代表官方 500 题成绩或总体优越性。
- 原始观测必须声明 `scripted`、`oracle` 或 `real-model`；一次报告拒绝混合模式。
  重复观测、伪造配对字段、不一致 checkpoint、验证奖励与结果冲突均拒绝。
- 中断且费用不确定时记录 `null` 与 UNKNOWN 次数，显示已知费用下界。
  策略总 API 成本包含其前缀；共同前缀不能跨物理分支重复相加来冒充实际账单。
  Codex 订阅用量另报，不能把 API 成本 0 解释为推理免费。
- 分析器验证输入一致性，不证明输入来自真实执行；原始日志、账本与产物关联仍须运行器提供。
  延迟、token 与恢复指标的自动汇总留待真实仓库运行器接入。

现有 M3 transport 的每请求 $0.062 预约额仅用于规划，**不是当前价格报价、账单保证或 M4 授权**：

| 范围 | 最多 API 请求 | API 预约总额 | Codex 订阅阶段 |
| --- | ---: | ---: | ---: |
| 4 题开发集 | 128 | $7.936 | 8 |
| 20 题 × 2 评测集 | 1,280 | $79.36 | 80 |

运行器与价格核验完成后再提交具体付费预算，获批才运行。之前 M3 的 $8 授权不覆盖这里。
本机镜像下载只使用现有网络与存储，没有租用云资源或启动 lab GPU。

## 零模型命令

从仓库根目录执行。元数据审计、计划与分析只需 Python 3.11 core；下载需要 Git 和网络：

```bash
.venv/bin/python -m rtv benchmark plan
.venv/bin/python -m rtv benchmark schedule --split evaluation
.venv/bin/python -m rtv benchmark budget --split pilot
.venv/bin/python -m rtv benchmark materialize --output work/m4-benchmark/harbor-datasets
.venv/bin/python -m rtv benchmark audit --upstream work/m4-benchmark/harbor-datasets
```

`materialize` 固定上游提交并 sparse checkout 24 个目录，随后核验所有文件。
已有目录若提交或内容不同则失败。`--plan` 可显式指定配置，须放在子命令之前。
CLI 默认配置面向本仓库 checkout；安装 wheel 后应显式传配置与旁边的 catalog。

真实容器预检需要 Docker；只允许开发集。构建时预装 verifier 依赖并生成 lock，测试时断网。
上游包装提交允许与 base commit 不同，但代码树必须相同且工作区干净：

```bash
.venv/bin/python scripts/m4_benchmark_preflight.py \
  --upstream work/m4-benchmark/harbor-datasets --task sympy__sympy-22914 \
  --output work/m4-benchmark/preflight-new
```

脚本依次在两个独立容器中运行未修改代码和上游参考补丁，检查 parser 报告、奖励和退出状态。
解析失败产生的 0 分不能算作有效负对照。只删除自己创建的容器；保留镜像、依赖 lock、
日志和哈希供核对。镜像初次构建要联网；锁文件记录本次解析，跨机器完全复现应保留派生镜像或复用锁，
不能只凭基础镜像固定就宣称整个构建可逐字节复现。

脚本统计验收、导出与输入格式示例：

```bash
.venv/bin/python scripts/m4_benchmark_acceptance.py --output work/m4-benchmark/statistics-new
.venv/bin/python -m rtv benchmark analyze --split pilot --mode scripted \
  --records work/m4-benchmark/statistics-new/records.jsonl --output work/m4-benchmark/report-new
```

每行 JSON 必须带 schedule 的 observation ID、protocol SHA、task ID、repeat、branch，
以及 `mode`、`status`、`checkpoint_id`、`prefix_api_cost_usd`、`incremental_api_cost_usd`、
`unknown_operations`。`solved/unsolved` 还要求 `verifier_status="completed"` 和一致的整数奖励
`verifier_reward=1/0`。其他状态为 `infra_error/source_failed/cancelled`。
输出目录不能已存在；报告包含原始 JSONL、输入 SHA256、冻结协议、完整 JSON/CSV 和中英文 Markdown。

## 已有证据与下一步

见[容器预检证据](evidence/m4-benchmark-preflight-arm64-20261011.json)与
[统计验收证据](evidence/m4-benchmark-protocol-20261011.json)。首个开发任务在 macOS ARM64
宿主上以 `linux/amd64` 运行，未修改代码为 0、参考补丁为 1，容器均清理。
这只证明一个任务的环境和 verifier 可用，没有 agent 调用，也不是 Kubernetes 结果。
早期权限、包装提交校验与退出码适配失败均保留。

任务感知的本机 runtime 已接入 `/testbed`，完成真实仓库 checkpoint 导出、独立恢复、
隐藏测试隔离、600 秒 verifier 和 16 请求 fresh 入口；见[真实仓库接入](m4-repositories.md)。
本节证据为脚本/参考解控制；后续真实模型会话验收见 pilot 指南。Kubernetes 资源、AMD64 执行、材料交付
及统计关联见[集群仓库接入](m4-repository-cluster.md)。[凭据通道与 pilot 启动器](m4-model-pilot.md)已完成一个任务的真实流程验收；首轮失败、第二轮以及 160 KB 配置的第三轮结果独立保存，第三轮四分支均 solved。全部开发环境预检与[四题工具额度对照](m4-tool-prefix-results.md)现已完成；正式评测仍待完成。
