# Baselines

Rows marked with a star in Tables 2-4 are our runs of each method's released code or weights on the
same requests, pools and metrics as SkillRep; Table 4 (SkillRet) is all our runs. All runs are scored
with the same R@10, MRR@10 and Hit@1 as SkillRep (`skillrep/metrics.py`; macro over SRA-Bench's six
source datasets).
Unless noted, requests are not truncated by characters and every encoder uses bf16, left padding,
last-token pooling, L2 normalization and a maximum length of 4,096 tokens.

| Method | Code / weights | Setting |
|---|---|---|
| BM25 | `skillrep/lexical.py` | Request only; index `name description body[:8000]`; k1 = 1.5, b = 0.75 (the lexical channel of SkillRep searched with the request alone). |
| Qwen3-Embedding-0.6B, untuned | [Qwen/Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) | Requests: `Instruct: Given a task description, retrieve the skill document that helps an agent complete the task\nQuery: ` + request (the model card's format); documents: body[:8000]. On SkillRet, documents are `name\ndescription\nbody`. Equivalent to `scripts/encode.py --instruction untuned` with all modules off. |
| SkillSight | [xiaojinying/SkillSight](https://github.com/xiaojinying/SkillSight) (commit 0e3ac86) | The official `SkillSightRetriever` unchanged (background subspace, effective rank, candidate union of 300, LEC with beta = 1); encoder: untuned Qwen3-Embedding-0.6B; documents `name\ndescription\nbody`; requests with the instruction above. |
| SkillRouter-Embedding | [pipizhao/SkillRouter-Embedding-0.6B](https://huggingface.co/pipizhao/SkillRouter-Embedding-0.6B), [code](https://github.com/zhengyanzhao1997/SkillRouter) | The official text formats: documents `name \| description[:300] \| body[:2500]`; requests `Instruct: Given a coding task description, retrieve the most relevant skill document that would help an agent complete the task\nQuery:` + request. With the official 1,500-character request truncation, the same code reproduces its published SkillsBench `easy` R@10 within 0.52; Tables 2 and 4 use untruncated requests. |
| Skill2Query | [MatZaharia/Skill2Query](https://github.com/MatZaharia/Skill2Query) (commit 1154ca0) | The released pseudo-queries (`results/pseudo_generation/pseudo_queries.jsonl`, no LLM calls) with SkillRouter-Embedding-0.6B, offline dense mode: each skill is indexed as `name \| description[:500] \| body[:8000]` plus one entry per template text (encoded as documents), and scores by its best entry. SRA-Bench official pool only; the pseudo-queries do not cover SkillRet's skills. |
| SkillRet-Embedding | [ThakiCloud/SKILLRET-Embedding-0.6B](https://huggingface.co/ThakiCloud/SKILLRET-Embedding-0.6B), [code](https://github.com/ThakiCloud/SKILLRET) (commit 0cc508e) | Fine-tuned on SkillRet's training split. Documents `name \| description \| skill_md` (the description when the body is empty); requests `Instruct: Given a skill search query, retrieve relevant skills that match the query\nQuery: ` + request; exact inner-product search. |
| SkillReason | [donghong1/SkillReason](https://github.com/donghong1/SkillReason), [donghongjiang/SkillReason-embedding-0.6b](https://huggingface.co/donghongjiang/SkillReason-embedding-0.6b) | Embedding model only (no reranker), top 50. SRA-Bench official pool: the official SRA evaluation (`skillreason.evaluation.benchmarks --benchmark sragents`), which reproduces the published R@10 of 88.18. UniPool: the same protocol, i.e. documents `name\ndescription\nbody`, whitespace-normalized requests, the official query prefix, through `skillreason.inference.retrieval --backend hf_last_token --query-prefix official`. SkillsBench and SkillRet: the retrieval command with its default document format. |

Rows without a star are copied from the cited papers: Capability Pages and Field-aware in Table 2,
and the R@10-only rows of Table 3 (from the SkillReason paper).

SkillRep's own rows: `scripts/run_skillrep.py` (full system) and `scripts/run_skillrep.py --w 0`
(without query-side expansion, which also removes the hypothetical skills from the lexical channel).
