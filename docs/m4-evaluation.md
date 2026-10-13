# M4：固定评测集的批量执行

[![中文](assets/lang-zh.svg)](m4-evaluation.md) [![English](assets/lang-en.svg)](m4-evaluation.en.md)

RTV 的正式执行入口使用已冻结的 20 个 SWE-bench Verified 任务，每题两次独立前缀、每次四个分支。
任务、顺序与重复次数来自 [v1 协议](../configs/benchmarks/swebench-verified-v1.json)，不依据模型结果替换题目。
这些配对结果用于描述执行策略；平台验收检查交接、隔离验证、记账和资源清理是否正确。

## 执行与费用

[`scripts/m4_evaluation.py`](../scripts/m4_evaluation.py) 串行准备环境、提交任务、结算并归档。
启动时冻结全部 160 条观察的分配、源文件指纹、执行镜像和总预算。每个配对运行先持久记录预约，
再启动模型。已知费用按实际用量结算；未知请求保留预约额；未结算的中断运行阻止继续花费。
重启协调器不会自动重跑已提交的付费任务。

当前使用已验证的 `mini-160kb-v1`：最多 32 个 API 请求，每个预约 $0.16，单配对预约 $5.12。
未使用的预约在结算后释放，因此不会把全部配对的最坏上限一次性计作实际花费。
总额度和此前已发生费用必须显式传入。Codex 订阅用量单独记录，不虚构其美元价格。

```bash
.venv/bin/python scripts/m4_evaluation.py \
  --config work/executor-config.json \
  --root work/m4-data/evaluation \
  --control work/evaluation-control \
  --environments work/evaluation-environments \
  --upstream work/m4-benchmark/harbor-datasets \
  --auth-file /absolute/private/codex-auth.json \
  --api-key-file /absolute/private/openai-api-key.txt \
  --approved-total-usd 30 --prior-cost-usd 0.85497195 \
  --stop-after-task django__django-11163
```

配置必须指向已部署的专用集群、不可变执行镜像和共享目录；命令中的路径是部署占位。
`--stop-after-task` 在该题两次重复结束后停止。它只限定此次执行范围，不修改完整评测名单；
其余题目仍显示尚未运行。删去此参数才会执行完整名单。

## 离线验证与产物

每题先做未修改代码和参考补丁的断网验证器对照。这些是环境检查，不是模型成绩。
某些上游评分器在 `make_test_spec` 中读取公开依赖文件。镜像准备阶段缓存固定 Git commit URL
的原始 HTTP 响应，运行时只重放经过 SHA256 校验的内容；未缓存或未固定版本的请求直接失败。
原始评分脚本和测试内容保持不变。评分配置通过构建时只读挂载传入，不写进 agent 镜像。

每次运行保留原始账本、轨迹引用和观察记录。资源释放后，将展开的工作区与检查点压缩归档，
逐文件读回验证字节、文件权限和符号链接，再移除展开副本。`archive.json` 记录归档哈希及恢复命令。
可重建的任务镜像交付包在该题两次重复结束后释放；镜像 ID 和交付包哈希仍保留。

运行进度位于控制目录的 `progress.json`，不可变报告位于 `reports/`。
缺失结果、环境问题、模型未解题和流程检查分别记录，不将它们互相替代。

归档校验不一致或删除失败时，协调器停止并保留归档与剩余副本；不会因此重跑模型。
