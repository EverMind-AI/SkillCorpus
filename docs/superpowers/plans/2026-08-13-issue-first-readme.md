# Issue-first README Positioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reframe the English and Chinese READMEs around a GitHub Issue to Agent Handoff product direction while preserving exact boundaries around what is available today.

**Architecture:** This is a documentation-only change. The two top-level READMEs remain structural mirrors, with a working SkillHub query and visible result before a compact, clearly labeled concept section after the current pipeline explanation.

**Tech Stack:** GitHub Flavored Markdown, HTML already used in the READMEs, shell-based validation.

## Global Constraints

- Do not claim that repository-aware issue analysis is currently shipped.
- Keep the entry-point table order: GitHub repo, Hugging Face artifacts, SkillHub.
- Keep language links below all badges.
- Use no em dash characters.
- Keep English and Chinese structure equivalent.

---

### Task 1: Reframe the English README

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: Existing release links, entry-point table, availability warning, and SkillHub demo.
- Produces: Outcome-led header copy and a clearly labeled issue-first product-direction section.

- [ ] **Step 1: Shorten the header promise**

Replace the current corpus-construction tagline with an outcome-led sentence that identifies the repository as the open foundation rather than a shipped issue-analysis product.

- [ ] **Step 2: Make the live result visible**

Add a browser link to the working SkillHub query and an abridged real result containing name, category, source, upstream URL, declared license, and quality score.

- [ ] **Step 3: Add the issue-first transformation**

Insert a compact concept section after the current pipeline explanation with this user journey:

```text
GitHub issue + repository snapshot
              -> task scope, cited files, repository checks, candidate skills
              -> review as Markdown, give to an agent, or paste into an Issue/PR
```

Describe the collaboration value for maintainers, contributors, and teams without exposing internal acquisition strategy.

- [ ] **Step 4: Draw the release boundary**

State that current SkillHub search and released retrieval artifacts work today, while repository-aware analysis, commit-pinned handoffs, and multi-skill packs are planned.

- [ ] **Step 5: Make today's demo explicit**

Rename the existing 30-second section so readers understand that its commands exercise the currently available workflow.

### Task 2: Mirror the change in Chinese

**Files:**
- Modify: `README.zh-CN.md`

**Interfaces:**
- Consumes: The approved English structure and the existing Chinese terminology.
- Produces: A natural Chinese version with the same claims, boundaries, ordering, and example content.

- [ ] **Step 1: Translate meaning, not English syntax**

Use concise Chinese product language, keep `Agent Handoff` understandable as `Agent 任务交接包`, and retain established technical terms where clearer.

- [ ] **Step 2: Match every factual boundary**

Mirror the present versus planned distinction, the zero-to-three skill limit, and the no-guarantee language.

### Task 3: Validate and publish

**Files:**
- Verify: `README.md`
- Verify: `README.zh-CN.md`

**Interfaces:**
- Consumes: The two edited READMEs.
- Produces: A clean documentation diff on the existing PR branch.

- [ ] **Step 1: Check structural parity and forbidden punctuation**

Run:

```bash
rg -n "—" README.md README.zh-CN.md
```

Expected: no matches.

- [ ] **Step 2: Check patch formatting**

Run:

```bash
git diff --check
```

Expected: exit code 0 with no output.

- [ ] **Step 3: Run configured documentation checks**

Run the repository's pre-commit configuration against both README files when available. If no Markdown-specific hook exists, record that fact and rely on the structural scans plus `git diff --check`.

- [ ] **Step 4: Review the final diff**

Confirm that no present-tense sentence claims the planned GitHub integration exists, that the existing 1K versus 96,401 availability distinction remains intact, and that the top table order is unchanged.

- [ ] **Step 5: Commit and push**

Stage only the two READMEs and the design/plan documents, commit with `docs: frame issue-first product direction`, and push the existing `docs/growth-first-readme` branch.
