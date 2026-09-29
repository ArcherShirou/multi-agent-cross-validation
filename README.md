# Multi-Agent Cross-Validation

这个项目尝试用交叉评审来改进 Agent 指令。一个 Agent 回答任务，其他 Agent 按预先写好的标准评审。发现问题后，只修改一份指令文件，再用同一批任务检查改动是否有效。

仓库目前是可运行的协议原型。默认示例用确定性脚本模拟 Agent，不调用模型接口，也不需要密钥。另有一份[人工模拟记录](examples/manual-simulation.md)，展示自然语言答案怎样被评审和修订；它不是独立多模型实验。[设计依据](docs/design-notes.md)单独列出。

## 运行

需要 Python 3.9 或更新版本，无第三方依赖。

```sh
python3 macv.py --rounds 2
python3 -m unittest discover -s tests -v
python3 compare_judges.py
# 选定版本后，用仓库外的测试文件运行一次
python3 macv.py --final-test /absolute/path/to/sealed.jsonl
```

第一次运行会从 `versions/0000` 生成两个新版本：第一轮修改 `skill.md`，第二轮修改 `agent.md`。结果写入 `last_run.json`，当前版本记在 `current.json`。如需从头再试，把 `current.json` 改回 `0000`，删除 `versions/0001` 和 `versions/0002`。

## 评审和版本规则

`protocol.json` 至少配置三个 Agent。每个任务轮流由其中一个生成答案，其余 Agent 评审。评审时提供任务、评分标准、答案和相关证据，不提供生成者身份或生成指令。运行前会用已知通过和失败的答案检查评审流程。

证据放在样例的 `evidence` 字段，包括相关规则 `policy`、运行编号 `run_id` 和工具结果 `tool_results`。如果评审识别出答案声称“链接已发送”，代码会检查成功结果的运行编号、调用编号、操作、对象和目标是否都匹配。只找到同名工具调用也不算完成。工具结果必须由可信的执行环境记录；让被评估的 Agent 自己填写结果，无法防止它伪造证据。

```json
{
  "rubric": {"actions": {"send_reset_link": {
    "description": "send a reset link", "name": "send_reset_link",
    "subject": "account:self", "target": "email:on_file"
  }}},
  "evidence": {"run_id": "run-123", "tool_results": [{
    "run_id": "run-123", "call_id": "call-456", "name": "send_reset_link",
    "subject": "account:self", "target": "email:on_file", "status": "success"
  }]}
}
```

修订者只收到训练任务的失败摘要：任务编号、答案和评审理由。每轮只能修改 `skill.md` 或 `agent.md` 中的一份，以便判断是哪项改动起了作用。候选版本必须在训练任务上取得更高的评审通过比例；开发期验证任务的通过比例不能下降，原本获得全部评审通过的任务也不能变为失败。未达到条件时不更新当前版本。

这里的分数按每一张评审票计算，所以有部分改善时可以继续下一轮；单个任务只有获得全部评审通过才算通过。评审返回 `uncertain` 时不自动更新版本。验证任务不会传给修订者。

定版后再准备一份独立 JSONL 测试集，放在仓库外，执行 `--final-test`。运行器要求测试编号与训练、验证编号互不重叠，只评估当前版本，不生成修订建议，并把版本、数据文件 SHA-256 和汇总分数写入忽略提交的 `final_test_result.json`。同一工作目录只能运行一次最终测试；要评估新版本，应另建干净工作目录和新测试集。测试集的保密仍依赖外部权限控制：本项目启动的命令并未受到文件系统隔离。

## 接入自己的 Agent

将 `protocol.json` 中的 `actors[].command` 换成可执行命令数组。运行器向命令的标准输入发送 JSON，请命令在标准输出返回一个 JSON 对象：

| 动作 | 输入 | 输出 |
| --- | --- | --- |
| `generate` | `actor`, `task`, `skill`, `agent` | `{"output": "..."}` |
| `review` | `actor`, `task`, `rubric`, `output`, `evidence` | `{"status": "pass", "reason": "...", "claimed_actions": []}` |
| `propose` | `actor`, `skill`, `agent`, `failures`, `train_score` | `{"target": "skill", "skill": "...", "agent": "..."}` |

`propose` 必须返回两份完整指令，并且只改动 `target` 指定的一份。模型密钥由你自己的命令进程读取，不要写进仓库文件。

可以单独给某个 Agent 配置 `review_command`，把评审交给别的程序；生成与修订仍用 `command`。`rubric.actions` 为每个操作指定描述、工具名、对象和目标，评审命令用 `claimed_actions` 返回答案声称已经完成的操作。运行器只接受 `pass`、`fail`、`uncertain` 三种状态。旧式的 `{"passed": true, "reason": "..."}` 仍可用，但定义了 `rubric.actions` 时必须补上 `claimed_actions`。

可选的 [Jev 评审命令](examples/jev_reviewer.py)用一次请求检查事实依据、是否回应问题，以及各个操作是否被声称已完成。它需要 Python 3.10+、`typesafe-sdk` 和 `TYPESAFE_API_KEY`。安装后，在所需 Agent 的配置中增加：

```json
"review_command": ["python3", "examples/jev_reviewer.py"]
```

当前仓库没有 Jev 密钥，未运行真实 Jev 请求。`0.2` 和 `0.8` 是演示阈值，需要用业务样例校准。可用 `compare_judges.py --command 'python3 examples/jev_reviewer.py'` 对同一批已标注样例统计误放、误拦、不确定数量、耗时和 token 数；默认命令运行本地模拟评审。比较两个版本时，应固定评审模型与评分规则；TypeSafe SDK 支持通过 `TYPESAFE_DEFAULT_MODEL` 指定模型。四条示例只用于检查接线，不能据此声称评审准确率。

## 使用范围

运行器信任 `protocol.json` 配置的命令，不隔离进程。Jev 一次返回多个判断，不等于多个独立评审。多个评审如果使用同一个模型和相似提示词，投票可能高度相关。反复试验同一份验证任务也会逐渐泄漏评估信息。接入真实模型时，需要独立的评审配置、受控的最终测试集，以及对随机输出的重复采样。
