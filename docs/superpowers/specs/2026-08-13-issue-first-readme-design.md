# Issue-first README positioning design

## Objective

Make SkillCorpus's highest-upside product direction understandable in one glance: a developer starts with a real GitHub issue and receives a reviewable agent handoff plus a small, source-backed skill pack.

This change is README positioning only. It must not imply that repository-aware issue analysis is already shipped.

## Audience and user value

- Maintainers can review whether an agent understood a task before code changes begin.
- Contributors can start unfamiliar work with relevant files, declared checks, open questions, and procedural skills in one place.
- Agent builders can reuse the corpus, models, API, and pipeline instead of assembling skill retrieval from zero.
- Enthusiasts can inspect, fork, and adapt a complete open skill-data and retrieval stack.

## Information architecture

Both `README.md` and `README.zh-CN.md` use the same order:

1. Title and a short outcome-led tagline.
2. Existing badges and language selector.
3. Existing three-entry table: GitHub repo, Hugging Face artifacts, then SkillHub.
4. The existing no-install SkillHub demo, followed by a real abridged API result and live browser link.
5. The current SkillCorpus pipeline and its factual trust boundaries.
6. A compact concept section showing the proposed GitHub Issue to Agent Handoff experience.
7. An explicit status boundary between the proposed experience and today's released capabilities.
8. A sample transformation containing task scope, cited files, repository checks, candidate skills, and Markdown sharing actions.

## Product promise

The primary future-facing sentence is:

> Paste a GitHub issue. Prepare a reviewable handoff for your agent.

The Chinese equivalent is:

> 粘贴一个 GitHub Issue，为你的 Agent 准备一份可审阅的任务交接包。

The repository-level entry remains secondary. A repository supplies context for an issue or produces a broader onboarding kit later; it is not presented as a universal readiness score.

## Trust boundaries

- Label the issue-first workflow as a product direction, not a released endpoint.
- State that SkillHub task search, corpus tooling, and released retrieval artifacts are available today.
- State that automatic repository analysis, commit-pinned handoffs, and multi-skill pack generation are not yet released.
- Do not use a scalar readiness score or promise that a selected skill will solve the issue.
- Separate observed repository evidence from inferred recommendations in the sample output.
- Present candidate skills as recommendations with rationale and provenance, not guaranteed selections.
- Keep existing third-party skill, license, safety, and execution caveats.

## Copy constraints

- English and Chinese content remain structurally equivalent.
- No em dash character appears in either README.
- The above-fold table order and language-link placement do not change.
- The public 1K corpus and unpublished 96,401-skill snapshot remain clearly distinguished.
- No new functionality, endpoint, or release date is promised.

## Validation

- Review the English and Chinese diffs side by side.
- Run both the live API query and the standard-library demo used in the quick start.
- Search both files for em dash characters and unsupported present-tense feature claims.
- Run `git diff --check`.
- Run the repository's available pre-commit checks against both README files if configured.
