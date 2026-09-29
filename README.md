# Multi-Agent Cross-Validation

这个项目尝试用交叉评审来改进 Agent 指令。一个 Agent 回答任务，其他 Agent 按预先写好的标准评审。发现问题后，只修改一份指令文件，再用同一批任务检查改动是否有效。

仓库目前是可运行的协议原型。默认示例用确定性脚本模拟 Agent，不调用模型接口，也不需要密钥。另有一份[人工模拟记录](examples/manual-simulation.md)，展示自然语言答案怎样被评审和修订；它不是独立多模型实验。[设计依据](docs/design-notes.md)单独列出。

## 运行

需要 Python 3.9 或更新版本，无第三方依赖。

```sh
python3 macv.py --rounds 2
python3 -m unittest discover -s tests -v
```

第一次运行会从 `versions/0000` 生成两个新版本：第一轮修改 `skill.md`，第二轮修改 `agent.md`。结果写入 `last_run.json`，当前版本记在 `current.json`。如需从头再试，把 `current.json` 改回 `0000`，删除 `versions/0001` 和 `versions/0002`。

## 评审和版本规则

`protocol.json` 至少配置三个 Agent。每个任务轮流由其中一个生成答案，其余 Agent 评审。评审时只提供任务、评分标准和答案，不提供生成者身份或生成指令。运行前会用已知通过和失败的答案检查评审者。

修订者只收到训练任务的失败摘要：任务编号、答案和评审理由。每轮只能修改 `skill.md` 或 `agent.md` 中的一份，以便判断是哪项改动起了作用。候选版本必须在训练任务上取得更高的评审通过比例；保留任务的通过比例不能下降，原本获得全部评审通过的任务也不能变为失败。未达到条件时不更新当前版本。

这里的分数按每一张评审票计算，所以有部分改善时可以继续下一轮；单个任务只有获得全部评审通过才算通过。保留任务不会传给修订者。

## 接入自己的 Agent

将 `protocol.json` 中的 `actors[].command` 换成可执行命令数组。运行器向命令的标准输入发送 JSON，请命令在标准输出返回一个 JSON 对象：

| 动作 | 输入 | 输出 |
| --- | --- | --- |
| `generate` | `actor`, `task`, `skill`, `agent` | `{"output": "..."}` |
| `review` | `actor`, `task`, `rubric`, `output` | `{"passed": true, "reason": "..."}` |
| `propose` | `actor`, `skill`, `agent`, `failures`, `train_score` | `{"target": "skill", "skill": "...", "agent": "..."}` |

`propose` 必须返回两份完整指令，并且只改动 `target` 指定的一份。模型密钥由你自己的命令进程读取，不要写进仓库文件。

## 使用范围

运行器信任 `protocol.json` 配置的命令，不隔离进程。多个评审如果使用同一个模型和相似提示词，投票可能高度相关。反复试验同一份保留任务也会逐渐泄漏评估信息。接入真实模型时，需要独立的评审配置、受控的保留集，以及对随机输出的重复采样。
