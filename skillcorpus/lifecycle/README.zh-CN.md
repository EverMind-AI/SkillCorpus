# 技能生命周期：按不带技能的水平退役

> 属于 **[SkillCorpus](../../README.zh-CN.md)** 框架的 `lifecycle` 阶段，接在 aggregate → curate → match → evaluate
> 之后。
>
> 论文 *Beyond Gain: Measuring Skill Internalization for Agent Skill Retirement* 的代码。

[English](README.md) · **简体中文**

公共技能库同时服务多个模型。模型变强以后，有些技能某个模型已经用不上了。这个阶段一次针对一个部署模型，判断它已经
**内化**了哪些技能（不带技能也能完成技能负责的事），并**只对这个模型**退役这些技能，技能本身仍留在库里。

比较"带技能运行"和"不带技能运行"的规则只读**增益**（带技能得分减去不带技能得分）。模型已经内化的技能增益接近零；
模型和技能都提供不了所需能力（**未覆盖**）时，增益同样接近零，所以只看增益的规则会把两者一起退掉。这个阶段改读
**不带技能的水平**：撤掉技能，让模型完成从该技能合成的任务并打分；只有当另一个模型带上该技能后在同一任务上得分
提高时，才信任高分。

## 流程

| 步骤 | 模块 | 作用 |
|---|---|---|
| 1 | `synthesize` | 为每个技能合成一个可执行任务和两份隐藏评分细则。生成器来自另一个模型家族；批评器剔除泄题或无法执行的任务；难度门对目标模型不带技能就能做出的任务重新生成。 |
| 2 | `run_arms` | 在**不带技能**（只给任务）或**带技能**（任务加技能）两臂上运行智能体，然后判分。 |
| 3 | `judge` | LLM 判官，依据工作区文件、运行轨迹和最终回复，按两份细则逐项打分；也可用于其他智能体框架产生的工作区。 |
| 4 | `capability_items` | 标出任务完成细则中需要技能文档里信息才能通过的题项（能力项）。 |
| 5 | `labels` | 计算 U 和 A（不带技能与带技能时能力项的平均得分）、有效性门和三种状态。 |
| 6 | `retire` | 按水平规则对每个技能、每个部署模型给出判决（论文算法 1）。 |
| — | `audit` | 在过门技能上分别应用只看增益的规则和水平规则，报告每条规则退掉了哪些状态的技能。 |
| — | `reproduce/` | 用论文的数据集复现论文表格，只需 Python 标准库。 |

每个模块都可以用 `python -m skillcorpus.lifecycle.<模块> --help` 查看用法。

## 依赖

```bash
pip install -e .                                   # 在仓库根目录
pip install -r skillcorpus/lifecycle/requirements.txt
```

步骤 1–4 调用 OpenAI 兼容的对话接口。`run_arms` 驱动 **Raven** 智能体，Raven 是单独的项目：用 `--raven-root`
（或环境变量 `RAVEN_ROOT`）指向一份 Raven 代码，做法与 [SkillsBench 运行器](../evaluate/skillsbench/README.md)
相同。步骤 5–6、`audit` 和 `reproduce/` 不需要任何接口。

> **请在隔离环境中运行智能体。** `run_arms` 让智能体以当前用户身份执行 shell 命令，且不限制在工作区内。任务可能安装
> 软件包或改动工作区以外的文件，请在容器或虚拟机中运行，至少使用单独的 `HOME`。

## 运行

任务目录结构为 `tasks/<task_id>/{task.md, inputs/, rubrics/task-completion.json,
rubrics/instruction-following.json}`，技能目录为 `skills/<skill>/SKILL.md`（及附带文件）。任务 id 的形式是
`tessl-single-<skill>_<idx>`，前缀表示单技能任务格式。

```bash
# 1. 从技能合成任务（生成器、批评器和难度门的接口）
export GEN_BASE_URL=... GEN_API_KEY=... GEN_MODEL=deepseek/deepseek-v4-flash
export CRITIC_BASE_URL=... CRITIC_API_KEY=... CRITIC_MODEL=Qwen3.6-27B
python -m skillcorpus.lifecycle.synthesize --skills-dir my_skills --sample 100 --seed 42 --out data

# 2-3. 目标模型和每个见证模型都跑两臂，每臂三次
#      （每个模型一个服务端点；SOLVER_API_KEY 是该端点的密钥）
export JUDGE_BASE_URL=... JUDGE_API_KEY=... JUDGE_MODEL=Qwen3.6-27B SOLVER_API_KEY=...
for model in Qwen3.6-27B Qwen3.5-397B; do for arm in unaided aided; do for r in 1 2 3; do
  python -m skillcorpus.lifecycle.run_arms --tasks-root data/tasks --skills-root data/skills \
      --arm $arm --model $model --api-base http://host:8000/v1 --raven-root ../raven \
      -o runs/$model/$arm/r$r
done; done; done

# 4. 能力项
export ADJ_BASE_URL=... ADJ_API_KEY=... ADJ_MODEL=Qwen3.5-397B
python -m skillcorpus.lifecycle.capability_items --tasks-root data/tasks --skills-root data/skills \
    -o capability_items.jsonl

# 5. 标签（每个已判分的运行目录给一组 --runs，覆盖所有模型）
python -m skillcorpus.lifecycle.labels --capability capability_items.jsonl \
    --runs Qwen3.6-27B unaided runs/Qwen3.6-27B/unaided/r1 ... -o labels.csv

# 6. 对一个部署模型给出判决；labels.csv 中的其他模型作为见证模型
python -m skillcorpus.lifecycle.retire --labels labels.csv --target Qwen3.6-27B -o decisions.csv
python -m skillcorpus.lifecycle.audit --labels labels.csv --target Qwen3.6-27B
```

各步骤用到的环境变量写在对应模块的文档字符串里。

### 判决

| 判决 | 含义与下一步 |
|---|---|
| `retire` | U ≥ τ = 0.8，且有见证模型从技能获益超过 γ = 0.1。对这个模型软退役：不再出现在该模型的检索结果中，技能仍留在库里，可以恢复。 |
| `extra_tasks` | U 距 τ 不超过 0.2，且任务不足三道。从同一生成批次再加两道题，各不带技能跑一次后重新判决。 |
| `regenerate` | U ≥ τ 但没有见证模型获益，任务可能没有考到技能。重新生成一次任务。 |
| `keep_no_verdict` | 重新生成的任务仍未通过有效性门。保留技能。 |
| `measure_aided` | U < 0.4。补跑带技能臂，检查技能本身是否有用。 |
| `keep_repair` | U 和 A 都低于 0.4，能力未覆盖。保留技能并标记为待修复。 |
| `keep` | 其他所有情况。 |

判决只对读出它的那个模型成立，技能库服务的每个模型都要分别读取。新模型可以复用任务、能力项和见证证据，只需补跑
自己的不带技能臂。

## 复现论文

论文的数据集包含 1,251 个任务及其隐藏细则、能力项标签、四个模型的逐项分数（目标模型 Qwen3.6-27B，见证模型
Qwen3.5-397B、DeepSeek-V4-Flash、Qwen3.8-27B）、135 个第二任务、SkillsBench 验证数据和判官复核，附数据说明与逐技能
许可。两个脚本读取数据集的本地副本。

```bash
python skillcorpus/lifecycle/reproduce/reproduce_labels.py path/to/dataset/scores   # 表 3
python skillcorpus/lifecycle/reproduce/reproduce_audits.py path/to/dataset          # 表 1、表 4、图 3、附录 C
```

论文中生成器为 DeepSeek-V4-Flash；批评器、难度门和判官为关闭思考的 Qwen3.6-27B；能力项裁决器为 Qwen3.5-397B；
智能体最多运行 40 轮。要按论文数据集的方式合成任务，还需设置 `PAPER_CRITIC=1`（见 `synthesize.py`）。

## 许可

本目录代码采用 MIT 许可（见 [LICENSE](LICENSE)）。数据集有各自的许可：任务、细则、标签和分数为 CC BY 4.0，
每个技能文本沿用其来源仓库的许可。
