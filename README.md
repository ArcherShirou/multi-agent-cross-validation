# Multi-Agent Cross-Validation (MACV)

一个极简、自带否决机制的 Agent 指令自迭代协议。生成 Agent 产出答案；其他 Agent 在不知道生成者身份及其指令的情况下独立评审；提案 Agent 只接收训练集的失败卡，每轮只修改 `skill.md` 或 `agent.md` 中的一个。候选版本同时通过训练增益与保留集无回退检查才会晋升。

项目包含[我亲自分角色完成的两轮手工模拟](examples/manual-simulation.md)和[设计依据](docs/design-notes.md)。手工模拟展示了真实自然语言答案与评审理由；可执行示例则使用确定性进程验证状态机，两者的结果不要混为模型实验。

## 协议

1. 至少配置三个有不同 `id` 的 Agent。每个 Agent 都能执行生成、评审、提案三个动作。正式评估前，先用已知通过/失败的样例校准评审者。
2. 对每个样例，轮流指定一个生成者，其余 Agent 独立评审；任一评审否决，该样例即失败。评审请求不含生成者身份和生成时的指令。
3. 把训练失败压缩为 `{case, output, reasons}`；提案者看不到保留集及完整调用轨迹。
4. 一次只修订 Skill 或 Agent 指令，便于归因。用同一批 Agent 重新评估候选指令。训练集通过率必须严格上升，且原先通过的每个保留集样例均不能退步，才写入新版本并原子更新 `current.json`。
5. 未晋升即停止；可通过 `--rounds` 限制最多迭代次数。

通过率按“样例 × 生成者 × 评审者”的通过票计，因此部分改善也可进入下一轮；单个样例的最终通过仍要求全部评审通过。保留集总分不能下降，原本全票通过的样例也不能变为失败。保留集不向提案请求发送。这个小项目默认信任所配置的进程和评审 Agent；它不提供进程隔离，也无法阻止同谋评审或通过重复试验对保留集过拟合。生产使用时，应把保留集存放在独立权限域，使用彼此独立的模型/提示词校准评审者，并周期性轮换保留集。若真实输出存在随机性，应多次采样，并按置信区间或更严格的业务阈值决策。

## 运行模拟示例

需要 Python 3.9+，无第三方依赖或模型密钥。

```sh
python3 macv.py --rounds 2
python3 -m unittest discover -s tests -v
```

模拟示例会把 `versions/0000` 依次晋升到 `versions/0001`、`versions/0002`。`last_run.json` 保存评分与训练失败卡；版本目录保存被晋升的 Skill 和 Agent 指令。要重新运行示例，将 `current.json` 中版本改回 `0000` 并删除生成的两个版本目录。

## 接入真实 Agent

修改 `protocol.json` 的 `actors[].command` 为各 Agent 的可执行命令数组。每次调用从标准输入接收一条 JSON 请求，向标准输出返回一条 JSON 对象：

| `action` | 请求字段 | 响应字段 |
| --- | --- | --- |
| `generate` | `actor`, `task`, `skill`, `agent` | `{"output": "..."}` |
| `review` | `actor`, `task`, `rubric`, `output` | `{"passed": true, "reason": "..."}` |
| `propose` | `actor`, `skill`, `agent`, `failures`, `train_score` | `{"target": "skill", "skill": "...", "agent": "..."}`，恰好修改一个目标 |

命令由仓库维护者配置，运行器不会执行模型 API 调用。若接入外部模型，在自己的命令进程中读取密钥；不要把密钥写进 `protocol.json`、版本文件或评审请求。真实评审最好采用彼此独立的模型、提示词或工具，并使用可机器验证的准则补充主观评分。
