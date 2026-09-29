# 设计依据（2026-09-29）

目标是缩短“发现失败 → 归因 → 修订”的周期，而不为每次试验保存完整轨迹。

| 来源 | 可核实的做法 | 本项目的取舍 |
| --- | --- | --- |
| [DSPy GEPA 文档](https://github.com/stanfordnlp/dspy/blob/main/docs/docs/api/optimizers/GEPA/overview.md) | 将文本反馈与分数一起用于反思式指令优化；支持按目标追踪候选 | 只保留去重的失败卡和评分摘要，提案只改一个指令目标；暂不实现 Pareto 候选池 |
| [OpenAI Evals 模板](https://github.com/openai/evals/blob/main/docs/eval-templates.md) | 对主观任务使用可解析的模型评审，对可精确判定的任务使用确定性检查 | 评审输出采用明确的布尔判定与理由；加入已知答案的校准样例 |
| [AgencyBench](https://github.com/GAIR-NLP/AgencyBench) | 用具体任务、交付物和 rubric 评估 Agent，可混合规则与模型评审 | 样例提供明确 rubric；业务方可以在适配器中加入规则、工具或模型评审 |
| [Anthropic Skill Creator](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md) | 迭代 Skill 时比较基线与候选，并检查实际输出 | 基线与候选在同一任务/评审集合上成对评估；单目标修订便于归因 |

## 核心判定

`promote = train_vote_score(candidate) > train_vote_score(current) AND holdout_vote_score(candidate) >= holdout_vote_score(current) AND no_holdout_pass_to_fail`

一个样例由一个生成 Agent 执行，其余 Agent 全部评审。任何一个评审失败，该样例失败。评审请求隐藏生成者 ID 和生成指令，但没有物理隔离：同一模型或相同提示词的多个评审可能高度相关。三名 Agent 的数量不能代替真正的异质性。

## 仍需验证的假设

1. 失败卡足以指导修订，且比保存完整轨迹更快、更便宜。要在真实任务上比较 token 成本、通过率和错误定位时间。
2. 单目标修订能够提升归因清晰度，但可能漏掉必须同时修改 Skill 和 Agent 才能生效的改动。可以先用逐轮修订解决，再评估是否需要成组提案。
3. 评审一致不代表评审正确。校准集必须覆盖业务上的高风险错误；真实部署需要人工抽检。
4. 保留集的通过/失败信号会在多轮运行中泄漏。生产系统应提供真正封存的最终测试集，并限制对同一集合的重复试验。

X 上未找到足够可核实、能直接改变实现决策的原始材料；因此这里仅列 GitHub 项目的一手说明，不把转述当作证据。
