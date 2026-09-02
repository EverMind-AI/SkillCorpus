// src/mcp.ts
import { argv } from "node:process";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

// src/config.ts
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
var CACHE_PATH_RE = /[/\\]plugins[/\\]cache[/\\]([^/\\]+)[/\\]/;
var MARKETPLACE_RE = /^[A-Za-z0-9._-]+$/;
var FALLBACK_MARKETPLACE = "skillcorpus-marketplace";
function validMarketplace(value) {
  return MARKETPLACE_RE.test(value) && value !== "." && value !== "..";
}
function marketplaceName(argv1 = process.argv[1] ?? "", env = process.env) {
  const override = env.SKILLSEARCH_MARKETPLACE?.trim();
  if (override && validMarketplace(override)) return override;
  const parsed = CACHE_PATH_RE.exec(argv1)?.[1];
  return parsed && validMarketplace(parsed) ? parsed : FALLBACK_MARKETPLACE;
}
function dataDirectory(argv1 = process.argv[1] ?? "", env = process.env, home = homedir()) {
  const override = env.SKILLSEARCH_DATA_DIR?.trim();
  if (override) {
    if (override === "~") return home;
    if (override.startsWith("~/") || override.startsWith("~\\")) return join(home, override.slice(2));
    return override;
  }
  return join(home, ".workbuddy-ai", "plugins", "data", `skillsearch-${marketplaceName(argv1, env)}`);
}
var DATA_DIR = dataDirectory();
var MAX_TIMEOUT_MS = 8e3;
var DEFAULTS = {
  // Both roots WorkBuddy actually keeps skills in: what the user installed,
  // and what plugins brought with them.
  skillsDirs: ["~/.workbuddy-ai/skills", "~/.workbuddy-ai/plugins/cache"],
  hubEndpoint: "https://skillhub.evermind.ai",
  hubApiKey: "",
  clawhubEndpoint: "https://clawhub.ai",
  skillhubCnEndpoint: "https://api.skillhub.cn",
  bundleCacheDir: "",
  model: "",
  modelBaseUrl: "https://api.openai.com/v1",
  modelApiKey: "",
  topK: 2,
  gatePool: 10,
  maxSelect: 2,
  indexBody: false,
  // Off, unlike every other host. A rewrite is a model round-trip inside the
  // gap between the user pressing enter and the reply starting, and this host
  // has no way to show that it is working.
  rewrite: false,
  gate: void 0,
  // ClawHub measured about 4s through the supported proxy. Keep enough room for
  // search plus one cached-or-downloaded body, while staying below the host’s
  // own 10s hook timeout so the hook can fail open first.
  timeoutMs: 8e3,
  availableTools: [],
  // Local first, catalog third. Tried the other way on 2026-08-18: the
  // catalog's top two for a poster task both depended on infrastructure this
  // machine does not have (a private ngrok MCP, a NANO_BANANA key), and the
  // model spent its whole reasoning budget on them while the local skill that
  // actually runs here sat unread in seat three. Curated-local beats
  // unvetted-remote wherever both have an answer; the catalog earns its seat
  // where local has nothing.
  localWeight: 1,
  hubWeight: 0.85,
  rrfK: 10,
  indexCachePath: join(DATA_DIR, "index-cache.json"),
  logPath: join(DATA_DIR, "skillsearch.log"),
  resolvePlaceholders: false,
  mode: "on_demand"
};
var ENV_KEYS = {
  skillsDirs: "SKILLSEARCH_SKILLS_DIRS",
  hubEndpoint: "SKILLSEARCH_HUB_ENDPOINT",
  hubApiKey: "SKILLSEARCH_HUB_API_KEY",
  clawhubEndpoint: "SKILLSEARCH_CLAWHUB_ENDPOINT",
  skillhubCnEndpoint: "SKILLSEARCH_SKILLHUB_CN_ENDPOINT",
  bundleCacheDir: "SKILLSEARCH_BUNDLE_CACHE_DIR",
  model: "SKILLSEARCH_MODEL",
  modelBaseUrl: "SKILLSEARCH_MODEL_BASE_URL",
  modelApiKey: "SKILLSEARCH_MODEL_API_KEY",
  topK: "SKILLSEARCH_TOP_K",
  gatePool: "SKILLSEARCH_GATE_POOL",
  maxSelect: "SKILLSEARCH_MAX_SELECT",
  indexBody: "SKILLSEARCH_INDEX_BODY",
  rewrite: "SKILLSEARCH_REWRITE",
  gate: "SKILLSEARCH_GATE",
  timeoutMs: "SKILLSEARCH_TIMEOUT_MS",
  availableTools: "SKILLSEARCH_AVAILABLE_TOOLS",
  localWeight: "SKILLSEARCH_LOCAL_WEIGHT",
  hubWeight: "SKILLSEARCH_HUB_WEIGHT",
  rrfK: "SKILLSEARCH_RRF_K",
  indexCachePath: "SKILLSEARCH_INDEX_CACHE_PATH",
  logPath: "SKILLSEARCH_LOG_PATH",
  resolvePlaceholders: "SKILLSEARCH_RESOLVE_PLACEHOLDERS",
  mode: "SKILLSEARCH_MODE"
};
function asList(value) {
  if (Array.isArray(value)) return value.map((entry) => String(entry).trim()).filter(Boolean);
  if (typeof value === "string") return value.split(",").map((entry) => entry.trim()).filter(Boolean);
  return void 0;
}
function asNumber(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return void 0;
}
function asBoolean(value) {
  if (typeof value === "boolean") return value;
  if (typeof value === "string") {
    const text2 = value.trim().toLowerCase();
    if (["true", "1", "yes", "on"].includes(text2)) return true;
    if (["false", "0", "no", "off"].includes(text2)) return false;
  }
  return void 0;
}
function asEndpoint(value, fallback) {
  return typeof value === "string" ? value.trim() : fallback;
}
function asText(value) {
  if (typeof value !== "string") return void 0;
  const trimmed = value.trim();
  return trimmed ? trimmed : void 0;
}
function readConfigDocument(path = join(DATA_DIR, "config.json")) {
  try {
    const parsed = JSON.parse(readFileSync(path, "utf8"));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}
function loadConfig(document, env = process.env) {
  const source = document ?? {};
  const pick = (key) => {
    const variable = ENV_KEYS[key];
    const fromEnv = variable ? env[variable] : void 0;
    return fromEnv !== void 0 && fromEnv !== "" ? fromEnv : source[key];
  };
  return {
    skillsDirs: asList(pick("skillsDirs")) ?? DEFAULTS.skillsDirs,
    hubEndpoint: asEndpoint(pick("hubEndpoint"), DEFAULTS.hubEndpoint),
    hubApiKey: asText(pick("hubApiKey")) ?? DEFAULTS.hubApiKey,
    clawhubEndpoint: asEndpoint(pick("clawhubEndpoint"), DEFAULTS.clawhubEndpoint),
    skillhubCnEndpoint: asEndpoint(pick("skillhubCnEndpoint"), DEFAULTS.skillhubCnEndpoint),
    bundleCacheDir: asText(pick("bundleCacheDir")) ?? DEFAULTS.bundleCacheDir,
    model: asText(pick("model")) ?? DEFAULTS.model,
    modelBaseUrl: asText(pick("modelBaseUrl")) ?? DEFAULTS.modelBaseUrl,
    modelApiKey: asText(pick("modelApiKey")) ?? DEFAULTS.modelApiKey,
    topK: asNumber(pick("topK")) ?? DEFAULTS.topK,
    gatePool: asNumber(pick("gatePool")) ?? DEFAULTS.gatePool,
    maxSelect: asNumber(pick("maxSelect")) ?? DEFAULTS.maxSelect,
    indexBody: asBoolean(pick("indexBody")) ?? DEFAULTS.indexBody,
    rewrite: asBoolean(pick("rewrite")) ?? DEFAULTS.rewrite,
    gate: asBoolean(pick("gate")),
    // Clamped below the host's own hook timeout in `hooks.json` (10s). Past
    // it the host kills the process first, and a killed hook fails the turn
    // rather than costing it its skills — the one outcome this plugin exists
    // to avoid. Two settings that must stay ordered, so the code orders them.
    timeoutMs: Math.min(asNumber(pick("timeoutMs")) ?? DEFAULTS.timeoutMs, MAX_TIMEOUT_MS),
    availableTools: asList(pick("availableTools")) ?? DEFAULTS.availableTools,
    localWeight: asNumber(pick("localWeight")) ?? DEFAULTS.localWeight,
    hubWeight: asNumber(pick("hubWeight")) ?? DEFAULTS.hubWeight,
    rrfK: asNumber(pick("rrfK")) ?? DEFAULTS.rrfK,
    indexCachePath: asText(pick("indexCachePath")) ?? DEFAULTS.indexCachePath,
    logPath: asText(pick("logPath")) ?? DEFAULTS.logPath,
    resolvePlaceholders: asBoolean(pick("resolvePlaceholders")) ?? DEFAULTS.resolvePlaceholders,
    // An unrecognised value falls back to the default rather than failing the
    // load: a typo should cost the deployment the mode it wanted, not its
    // whole plugin config.
    mode: pick("mode") === "auto" ? "auto" : "on_demand"
  };
}

// src/retrieve.ts
import { homedir as homedir2 } from "node:os";
import { join as join8 } from "node:path";

// ../engine-typescript/src/engine.ts
import { createHash } from "node:crypto";

// ../engine-typescript/src/fusion.ts
var RRF_K = 60;
function rrfMergeWeighted(sourceResults, k, dedupBy = "name", rrfK = RRF_K) {
  const merged = /* @__PURE__ */ new Map();
  for (const { name: sourceName2, weight, hits } of sourceResults) {
    for (const [i, hit] of hits.entries()) {
      const rank = i + 1;
      const key = hit[dedupBy];
      const contribution = weight / (rrfK + rank);
      const seen = merged.get(key);
      if (seen === void 0) {
        merged.set(key, {
          score: contribution,
          best: hit,
          bestClaim: contribution,
          sources: [sourceName2]
        });
        continue;
      }
      seen.score += contribution;
      seen.sources.push(sourceName2);
      if (contribution > seen.bestClaim) {
        seen.best = hit;
        seen.bestClaim = contribution;
      }
    }
  }
  return [...merged.values()].sort((a, b) => b.score - a.score).slice(0, k).map(({ score, best, sources }) => ({
    ...best,
    meta: { ...best.meta, rrfScore: score, contributingSources: [...sources] }
  }));
}

// ../engine-typescript/src/deadline.ts
function withTimeout(promise, ms) {
  let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => {
      reject(new Error(`timed out after ${ms}ms`));
    }, ms);
  });
  return Promise.race([promise, deadline]).finally(() => {
    clearTimeout(timer);
  });
}
async function bounded(run, ms, outer) {
  const controller = new AbortController();
  const onOuterAbort = () => {
    controller.abort();
  };
  outer?.addEventListener("abort", onOuterAbort, { once: true });
  const attempt = run(controller.signal);
  attempt.catch(() => {
  });
  try {
    return await withTimeout(attempt, ms);
  } catch (error) {
    controller.abort();
    throw error;
  } finally {
    outer?.removeEventListener("abort", onOuterAbort);
  }
}

// ../engine-typescript/src/replies.ts
var THINK = /<think>[\s\S]*?<\/think>/g;
var FENCED = /```(?:json)?\s*\n?([\s\S]*?)\n?```/;
var BRACED = /\{[\s\S]*\}/;
function extractJsonObject(content) {
  const text2 = (content ?? "").replace(THINK, "").trim();
  if (!text2) return void 0;
  const candidates = [];
  const fenced = FENCED.exec(text2);
  if (fenced?.[1] !== void 0) candidates.push(fenced[1].trim());
  const braced = BRACED.exec(text2);
  if (braced) candidates.push(braced[0]);
  candidates.push(text2);
  for (const candidate of candidates) {
    let data;
    try {
      data = JSON.parse(candidate);
    } catch {
      continue;
    }
    if (typeof data === "object" && data !== null && !Array.isArray(data)) {
      return data;
    }
  }
  return void 0;
}

// ../engine-typescript/src/gate.ts
var BODY_EXCERPT_CHARS = 300;
var LLMGateFilter = class {
  model;
  maxSelect;
  fallbackTopK;
  timeoutMs;
  constructor(model, options = {}) {
    this.model = model;
    this.maxSelect = options.maxSelect ?? 2;
    this.fallbackTopK = options.fallbackTopK ?? this.maxSelect;
    this.timeoutMs = options.timeoutMs ?? 2e4;
  }
  /**
   * Narrow `candidates` to the skills worth injecting for `task`.
   *
   * `availableTools` enables the environment check; without it the gate still
   * judges relevance. Returns the top `fallbackTopK` candidates on timeout,
   * transport failure, or an unparseable reply — a broken gate degrades to
   * unfiltered retrieval rather than to silence.
   *
   * @param task - the user's words, unrewritten: the gate judges the real ask.
   * @param candidates - the fused pool, best first.
   * @param availableTools - tools this agent can call, enabling the hard rule.
   * @param signal - aborts the call when the turn is cancelled.
   * @returns the kept candidates, at most `maxSelect`, possibly empty.
   */
  async filter(task, candidates, availableTools, signal) {
    if (candidates.length === 0) return [];
    const { catalog, byId } = buildCatalog(candidates);
    const prompt = this.buildPrompt(task, catalog, availableTools);
    let content;
    try {
      content = await bounded(
        (s) => this.model.complete(prompt, { signal: s }),
        this.timeoutMs,
        signal
      );
    } catch {
      return candidates.slice(0, this.fallbackTopK);
    }
    let selectedIds;
    try {
      selectedIds = parseResponse(content);
    } catch {
      return candidates.slice(0, this.fallbackTopK);
    }
    const selected = [];
    for (const id of selectedIds.slice(0, this.maxSelect)) {
      const hit = byId.get(id);
      if (hit) selected.push(hit);
    }
    return selected;
  }
  buildPrompt(task, catalog, availableTools) {
    let toolsBlock = "";
    if (availableTools && availableTools.length > 0) {
      const names = [...new Set(availableTools)].sort().join(", ");
      toolsBlock = `# Agent Tools

The agent's ONLY available tools are: ${names}.

**Hard rule**: a skill is NOT relevant if its workflow requires any tool, file, or environment that the agent lacks. Inspect EACH candidate's body excerpt and exclude it if you see any of:
- A specific external API / SDK / vendor (e.g. \`\`nyne-deep-research\`\`, \`\`musicbrainz\`\`, \`\`bandcamp\`\`, \`\`-api\`\` suffix, vendor wrapper).
- Environment placeholders or paths that won't exist in this runtime: \`\`\${CLAUDE_PLUGIN_ROOT}\`\`, \`\`{baseDir}\`\`, \`\`{overrides}\`\`, \`\`.aiwg/\`\`, \`\`\${SKILL_HOME}\`\`, \`\`$ARGUMENTS\`\` as a slot, references to \`\`\${...}\`\` template variables.
- Slash-command triggers (e.g. \`\`/research-query\`\`) \u2014 the agent has no slash dispatcher.
- \`\`Parent agent:\`\` style multi-agent framework assumptions, or references to other SKILL.md files under unspecified directories.
- Agent personas, role-play, creative writing, content generation \u2014 these are not research procedures.

**Only include** skills whose body describes a self-contained procedure that the agent can execute with just the listed tools (e.g. query-writing strategies, verification workflows, search-result interpretation).

`;
    }
    return `You are a skill selector for an autonomous agent.

# Task

${task}

` + toolsBlock + `# Candidate Skills

${catalog}

# Instructions

1. **Plan**: briefly think about what the task requires and which sequence of available-tool calls would achieve it.
2. **Filter**: for EACH candidate skill, ask "can the agent execute this skill's workflow using only the available tools above?" If no, drop it \u2014 no matter how topically relevant.
3. **Match**: among the survivors, a skill is relevant ONLY if it provides a procedure or strategy directly useful for a core part of your plan. Vague topical overlap is not enough.
4. **Decide**: select AT MOST ${this.maxSelect} skill(s). If no skill survives both the tool check and the relevance check, you MUST return an empty list. Selecting an irrelevant or unexecutable skill is strictly worse than selecting none.

Return ONLY a JSON object on a single line:
{"plan": "1-sentence plan", "skills": ["qualified_id_1"]}

Or when nothing applies: {"plan": "...", "skills": []}

Use the EXACT qualified_id strings from the candidate list above.`;
  }
};
function buildCatalog(candidates) {
  const lines = [];
  const byId = /* @__PURE__ */ new Map();
  for (const h of candidates) {
    const sid = h.qualifiedId;
    let desc = (h.meta.description ?? "").trim().replace(/\n/g, " ");
    if (!desc) desc = "(no description)";
    if (desc.length > 200) desc = `${desc.slice(0, 197)}...`;
    const body = h.content.trim();
    const excerpt = body.split(/\s+/).join(" ").slice(0, BODY_EXCERPT_CHARS) || "(no body)";
    lines.push(`- ${sid}: ${desc}
  Body excerpt: ${excerpt}`);
    byId.set(sid, h);
  }
  return { catalog: lines.join("\n"), byId };
}
function parseResponse(content) {
  const data = extractJsonObject(content);
  if (data === void 0) throw new Error("no JSON object in reply");
  const skills = data.skills;
  if (!Array.isArray(skills)) throw new Error("missing 'skills' array");
  return skills.filter((s) => typeof s === "string" && s.length > 0);
}

// ../engine-typescript/src/refs.ts
import { existsSync, statSync } from "node:fs";
import { dirname, join as join2 } from "node:path";
var BUNDLED_DIRS = ["references", "scripts", "assets", "examples"];
var MD_LINK_RE = new RegExp(
  String.raw`\[([^\]]+)\]\((?:\.{0,2}/)?((?:${BUNDLED_DIRS.join("|")})/[^)\s]+)\)`,
  "g"
);
var BASE_DIR_REF_RE = /\{baseDir\}\/(\S+?)(?=[\s)'"`]|$)/g;
var BARE_BASE_DIR_RE = /\{baseDir\}(?!\/)/g;
var CODE_FENCE_RE = /(```[\s\S]*?```)/;
function resolveRefs(body, skillDir) {
  if (!body) return { body: "", anyResolved: false };
  const hasDir = !!skillDir && isDirectory(skillDir);
  if (!hasDir) {
    const stripped = body.includes("{baseDir}") ? body.replaceAll("{baseDir}/", "").replaceAll("{baseDir}", "") : body;
    return { body: stripped, anyResolved: false };
  }
  const baseDir = skillDir;
  let anyResolved = false;
  const mdSub = (match, label, rel) => {
    const trimmed = rel.replace(/[.,;:]+$/, "");
    const cut = firstIndexOfAny(trimmed, ["#", "?"]);
    const fragment = cut === -1 ? "" : trimmed.slice(cut);
    const relFile = cut === -1 ? trimmed : trimmed.slice(0, cut);
    if (relFile && existsSync(join2(baseDir, relFile))) {
      anyResolved = true;
      return `[${label}](${baseDir}/${relFile}${fragment})`;
    }
    return match;
  };
  const segments = body.split(CODE_FENCE_RE);
  let out = segments.map((segment) => segment.startsWith("```") ? segment : segment.replace(MD_LINK_RE, mdSub)).join("");
  if (out.includes("{baseDir}")) {
    out = out.replace(BASE_DIR_REF_RE, (match, ref) => {
      const trimmed = ref.replace(/[.,;:]+$/, "");
      if (trimmed && existsSync(join2(baseDir, trimmed))) {
        anyResolved = true;
        return `${baseDir}/${ref}`;
      }
      return match;
    });
    out = out.replace(BARE_BASE_DIR_RE, () => {
      anyResolved = true;
      return baseDir;
    });
  }
  return { body: out, anyResolved };
}
function isDirectory(path) {
  try {
    return statSync(path).isDirectory();
  } catch {
    return false;
  }
}
function firstIndexOfAny(text2, needles) {
  const found = needles.map((n) => text2.indexOf(n)).filter((i) => i !== -1);
  return found.length === 0 ? -1 : Math.min(...found);
}
var PLACEHOLDER_RE = /\{\{([A-Z_]+)(?::([A-Za-z0-9._-]+))?\}\}/g;
function resolvePlaceholders(body, skillDir, runtime = {}) {
  if (!body || !body.includes("{{")) return body;
  const sd = skillDir || void 0;
  if (sd) {
    body = body.replaceAll("{{SKILL_DIR}}/", `${sd.replace(/\/+$/, "")}/`);
    body = body.replaceAll("{{SKILL_DIR}}", sd);
  }
  return body.replace(PLACEHOLDER_RE, (match, name, arg) => {
    if (name === "SKILL_DIR") {
      return sd && arg && arg !== "." && arg !== ".." ? join2(dirname(sd), arg) : match;
    }
    if (name === "AGENT_STATE_DIR") {
      return runtime.stateDir || runtime.outputDir || match;
    }
    if (name === "HOME") {
      return runtime.homeDir || runtime.outputDir || match;
    }
    if (name === "OUTPUT_DIR") {
      return runtime.outputDir || match;
    }
    return match;
  });
}

// ../engine-typescript/src/rewriter.ts
var REWRITE_PROMPT = `Rewrite the following user query for skill retrieval. Remove noise (paths, IDs, timestamps, boilerplate). Keep task type, domain, required capabilities, and key technical details. Do NOT answer or solve the query \u2014 only rewrite it.

Return JSON: {"rewritten_query": "..." or null}

{query}`;
var QUERY_MAX_LENGTH = 2e3;
var TIMEOUT_MS = 5e3;
var QueryRewriter = class {
  model;
  timeoutMs;
  constructor(model, options = {}) {
    this.model = model;
    this.timeoutMs = options.timeoutMs ?? TIMEOUT_MS;
  }
  /**
   * Rewrite `query` for retrieval.
   *
   * @param query - the user's words for this turn.
   * @param signal - aborts the call when the turn is cancelled.
   * @returns the rewrite, or an empty one meaning "search the raw query".
   *   A blank query, a transport failure and an unparsable reply all land
   *   there; none of them stops the search.
   */
  async analyze(query, signal) {
    const truncated = query.trim().slice(0, QUERY_MAX_LENGTH);
    if (!truncated) return { rewrittenQuery: "" };
    const prompt = REWRITE_PROMPT.replace("{query}", () => truncated);
    let content;
    try {
      content = await bounded(
        (s) => this.model.complete(prompt, { signal: s }),
        this.timeoutMs,
        signal
      );
    } catch {
      return { rewrittenQuery: "" };
    }
    return parse(content);
  }
};
function parse(content) {
  const data = extractJsonObject(content);
  if (data === void 0) return { rewrittenQuery: "" };
  const record = data;
  const rewritten = typeof record.rewritten_query === "string" ? record.rewritten_query.trim() : "";
  return { rewrittenQuery: rewritten };
}

// ../engine-typescript/src/engine.ts
var SkillSearchEngine = class {
  sources;
  rewriter;
  gate;
  fetchBody;
  materialise;
  onDiagnostic;
  topK;
  rrfK;
  gatePool;
  overFetch;
  perSourceMax;
  dedupBy;
  heading;
  refs;
  placeholders;
  runtime;
  constructor(parts, options = {}) {
    this.sources = parts.sources;
    this.rewriter = parts.rewriter;
    this.gate = parts.gate;
    this.fetchBody = parts.fetchBody;
    this.materialise = parts.materialise;
    this.onDiagnostic = parts.onDiagnostic;
    this.topK = options.topK ?? 2;
    this.rrfK = options.rrfK;
    this.gatePool = options.gatePool ?? 10;
    this.overFetch = options.overFetch ?? 2;
    this.perSourceMax = options.perSourceMax ?? 2;
    this.dedupBy = options.dedupBy ?? "qualifiedId";
    this.heading = options.heading ?? "# Skills";
    this.refs = options.resolveRefs ?? true;
    this.placeholders = options.resolvePlaceholders ?? false;
    this.runtime = {
      ...options.outputDir === void 0 ? {} : { outputDir: options.outputDir },
      ...options.homeDir === void 0 ? {} : { homeDir: options.homeDir },
      ...options.stateDir === void 0 ? {} : { stateDir: options.stateDir }
    };
  }
  /** Whether anything is configured to search. */
  get enabled() {
    return this.sources.length > 0;
  }
  /**
   * Search for `query` and render what survives.
   * @param query - the user's words for this turn.
   * @param options - this turn's cancellation and tool list.
   * @returns the block to inject, or `''` when this turn gets no skills.
   */
  async retrieve(query, options = {}) {
    const hits = await this.hits(query, options);
    return hits.length === 0 ? "" : this.render(hits);
  }
  /**
   * Run the pipeline and return the selection unrendered.
   * @param query - the user's words for this turn.
   * @param options - this turn's cancellation and tool list.
   * @returns the selected skills, empty on any failure; never rejects.
   */
  async hits(query, options = {}) {
    if (!this.enabled || !query.trim()) return [];
    try {
      return await this.run(query, options);
    } catch {
      return [];
    }
  }
  async run(query, options) {
    const signal = options.signal;
    let searchQuery = query;
    if (this.rewriter) {
      const { rewrittenQuery } = await this.rewriter.analyze(query, signal);
      if (rewrittenQuery) searchQuery = rewrittenQuery;
    }
    const poolSize = this.gate ? this.gatePool : this.topK;
    const perSource = Math.min(this.perSourceMax, poolSize * this.overFetch);
    const results = await Promise.all(
      this.sources.map(async (source) => {
        const startedAt = Date.now();
        try {
          const hits2 = await source.search(searchQuery, signal ? { signal } : {}, perSource);
          this.diagnose({
            source: source.name,
            stage: "search",
            elapsedMs: Date.now() - startedAt,
            hitCount: hits2.length
          });
          return { name: source.name, weight: source.weight, hits: hits2 };
        } catch (error) {
          this.diagnose({
            source: source.name,
            stage: "search",
            elapsedMs: Date.now() - startedAt,
            hitCount: 0,
            error: errorMessage(error)
          });
          return { name: source.name, weight: source.weight, hits: [] };
        }
      })
    );
    let hits = this.rrfK === void 0 ? rrfMergeWeighted(results, poolSize, this.dedupBy) : rrfMergeWeighted(results, poolSize, this.dedupBy, this.rrfK);
    if (hits.length === 0) return [];
    hits = await this.hydrateBodies(hits, signal);
    hits = hits.filter((hit) => !["clawhub", "skillhub_cn"].includes(String(hit.meta.source)) || Boolean(hit.content));
    hits = dedupExactBodies(hits);
    if (hits.length === 0) return [];
    hits = this.resolveLocalRefs(hits);
    if (this.gate) {
      hits = await this.gate.filter(query, hits, options.availableTools, signal);
    }
    return this.resolvePlaceholders(await this.resolveHitRefs(hits.slice(0, this.topK), signal));
  }
  diagnose(diagnostic) {
    try {
      this.onDiagnostic?.(diagnostic);
    } catch {
    }
  }
  /** Rewrite refs for hits that already know their directory. */
  resolveLocalRefs(hits) {
    if (!this.refs) return hits;
    return hits.map((hit) => {
      const skillDir = hit.meta.skillDir;
      if (typeof skillDir !== "string" || !skillDir || !hit.content) return hit;
      const { body } = resolveRefs(hit.content, skillDir);
      return body === hit.content ? hit : { ...hit, content: body };
    });
  }
  /**
   * Give each survivor a directory, then rewrite its refs against it.
   *
   * A local hit was already resolved before the gate; this pass exists for
   * the remote ones, whose bundle is extracted first when the host
   * supplied a way to. A failure there leaves the body unresolved.
   */
  async resolveHitRefs(hits, signal) {
    if (!this.refs) return hits;
    return Promise.all(hits.map(async (hit) => {
      let current = hit;
      if (typeof current.meta.skillDir !== "string" && this.materialise) {
        const startedAt = Date.now();
        const source = sourceName(current);
        try {
          const installed = await this.materialise(current, signal);
          this.diagnose({
            source,
            stage: "materialise",
            elapsedMs: Date.now() - startedAt,
            succeeded: Boolean(installed)
          });
          if (installed) {
            current = {
              ...current,
              content: installed.body || current.content,
              meta: { ...current.meta, skillDir: installed.dir }
            };
          }
        } catch (error) {
          this.diagnose({
            source,
            stage: "materialise",
            elapsedMs: Date.now() - startedAt,
            succeeded: false,
            error: errorMessage(error)
          });
          return current;
        }
      }
      const skillDir = current.meta.skillDir;
      if (typeof skillDir !== "string" || !skillDir || !current.content) return current;
      const { body } = resolveRefs(current.content, skillDir);
      return body === current.content ? current : { ...current, content: body };
    }));
  }
  /**
   * Fill PathGuard placeholders (`{{SKILL_DIR}}`, `{{HOME}}`, …) per agent.
   *
   * Unlike `resolveLocalRefs` / `resolveHitRefs` this never touches the
   * filesystem and is not gated by `resolveRefs`: a placeholder already names
   * its target, and only the host knows it. It runs last, once every surviving
   * hit has its `skillDir` settled.
   */
  resolvePlaceholders(hits) {
    if (!this.placeholders) return hits;
    return hits.map((hit) => {
      const content = hit.content;
      const source = String(hit.meta.source ?? "");
      const trusted = ["local", "builtin", "hub"].includes(source) || hit.meta.pathguardProcessed === true;
      if (!trusted || !content || !content.includes("{{")) return hit;
      const skillDir = typeof hit.meta.skillDir === "string" ? hit.meta.skillDir : void 0;
      const body = resolvePlaceholders(content, skillDir, this.runtime);
      return body === content ? hit : { ...hit, content: body };
    });
  }
  /** Fill in bodies for hits a source returned as metadata only. */
  async hydrateBodies(hits, signal) {
    const fetchBody = this.fetchBody;
    if (!fetchBody) return hits;
    return Promise.all(
      hits.map(async (hit) => {
        if (hit.content) return hit;
        const startedAt = Date.now();
        const source = sourceName(hit);
        try {
          const out = await fetchBody(hit, signal);
          this.diagnose({
            source,
            stage: "hydrate",
            elapsedMs: Date.now() - startedAt,
            succeeded: Boolean(out && (typeof out === "string" || out.body))
          });
          if (!out) return hit;
          if (typeof out === "string") return { ...hit, content: out };
          const next = { ...hit };
          if (out.body) next.content = out.body;
          if (out.record) next.meta = { ...hit.meta, _fetched: out.record };
          return next;
        } catch (error) {
          this.diagnose({
            source,
            stage: "hydrate",
            elapsedMs: Date.now() - startedAt,
            succeeded: false,
            error: errorMessage(error)
          });
          return hit;
        }
      })
    );
  }
  /**
   * Render hits into the injected block.
   *
   * A hit whose files are on disk gets its directory named and a sentence
   * telling the model how to reach them; a body saying `scripts/x.sh` is
   * otherwise read as relative to the agent's cwd.
   *
   * @param hits - the selection, in the order the model should see it.
   * @returns the model-facing block, or `''` when no hit carried a body.
   */
  render(hits) {
    const parts = [];
    for (const hit of hits) {
      const skillDir = hit.meta.skillDir;
      const header = skillDir ? `### Skill: ${hit.name}  [${hit.qualifiedId}]
**Skill directory**: \`${skillDir}\`
Relative refs (e.g. \`references/x.md\`, \`./scripts/y.sh\`) resolve under this directory \u2014 use the absolute form for read_file / exec.
` : `### Skill: ${hit.name}  [${hit.qualifiedId}]
`;
      parts.push(header);
      const content = hit.content.trim();
      if (content) parts.push(content);
    }
    const body = parts.join("\n\n");
    return body ? `${this.heading}

${body}` : "";
  }
};
function dedupExactBodies(hits) {
  const output = [];
  const positions = /* @__PURE__ */ new Map();
  for (const hit of hits) {
    const body = normaliseBody(hit.content);
    if (!body) {
      output.push(hit);
      continue;
    }
    const digest = createHash("sha256").update(body).digest("hex");
    const existing = positions.get(digest);
    if (existing === void 0) {
      positions.set(digest, output.length);
      output.push(hit);
    } else if (isLocal(hit) && !isLocal(output[existing])) {
      output[existing] = hit;
    }
  }
  return output;
}
function normaliseBody(body) {
  return body.replace(/\r\n?/g, "\n").split("\n").map((line) => line.trimEnd()).join("\n").trim();
}
function isLocal(hit) {
  return String(hit.meta.source) === "local" || typeof hit.meta.skillDir === "string" && Boolean(hit.meta.skillDir);
}
function sourceName(hit) {
  const source = hit.meta.source;
  return typeof source === "string" && source ? source : hit.qualifiedId.split("/", 1)[0] || "unknown";
}
function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

// ../engine-typescript/src/hub-source.ts
import { existsSync as existsSync2 } from "node:fs";
import { join as join4 } from "node:path";

// ../engine-typescript/src/bundle.ts
import { mkdir, rename, rm, writeFile } from "node:fs/promises";
import { readdir } from "node:fs/promises";
import { isAbsolute, join as join3, relative, resolve } from "node:path";

// ../engine-typescript/src/zip.ts
import { inflateRawSync } from "node:zlib";
var EOCD_SIGNATURE = 101010256;
var CENTRAL_SIGNATURE = 33639248;
var LOCAL_SIGNATURE = 67324752;
var STORED = 0;
var DEFLATED = 8;
function findEndOfCentralDirectory(buffer) {
  const minimum = 22;
  if (buffer.length < minimum) throw new Error("not a zip archive: too short");
  const earliest = Math.max(0, buffer.length - minimum - 65535);
  for (let offset = buffer.length - minimum; offset >= earliest; offset -= 1) {
    if (buffer.readUInt32LE(offset) === EOCD_SIGNATURE) return offset;
  }
  throw new Error("not a zip archive: no end-of-central-directory record");
}
function readName(buffer, start, length) {
  if (start + length > buffer.length) throw new Error("zip entry name runs past the archive");
  return buffer.toString("utf8", start, start + length);
}
function readZipEntries(buffer) {
  const eocd = findEndOfCentralDirectory(buffer);
  const entryCount = buffer.readUInt16LE(eocd + 10);
  const directoryOffset = buffer.readUInt32LE(eocd + 16);
  if (directoryOffset > buffer.length) throw new Error("zip central directory is out of range");
  const entries = [];
  let cursor = directoryOffset;
  for (let index = 0; index < entryCount; index += 1) {
    if (cursor + 46 > buffer.length) throw new Error("zip central directory is truncated");
    if (buffer.readUInt32LE(cursor) !== CENTRAL_SIGNATURE) {
      throw new Error(`zip central directory entry ${index} has a bad signature`);
    }
    const method = buffer.readUInt16LE(cursor + 10);
    const compressedSize = buffer.readUInt32LE(cursor + 20);
    const declaredSize = buffer.readUInt32LE(cursor + 24);
    const nameLength = buffer.readUInt16LE(cursor + 28);
    const extraLength = buffer.readUInt16LE(cursor + 30);
    const commentLength = buffer.readUInt16LE(cursor + 32);
    const localOffset = buffer.readUInt32LE(cursor + 42);
    const name = readName(buffer, cursor + 46, nameLength);
    cursor += 46 + nameLength + extraLength + commentLength;
    if (name.endsWith("/")) continue;
    entries.push({
      name,
      declaredSize,
      read() {
        if (method !== STORED && method !== DEFLATED) {
          throw new Error(`${name}: unsupported compression method ${method}`);
        }
        if (localOffset + 30 > buffer.length) throw new Error(`${name}: local header out of range`);
        if (buffer.readUInt32LE(localOffset) !== LOCAL_SIGNATURE) {
          throw new Error(`${name}: bad local header signature`);
        }
        const localNameLength = buffer.readUInt16LE(localOffset + 26);
        const localExtraLength = buffer.readUInt16LE(localOffset + 28);
        const start = localOffset + 30 + localNameLength + localExtraLength;
        const end = start + compressedSize;
        if (end > buffer.length) throw new Error(`${name}: data runs past the archive`);
        const raw = buffer.subarray(start, end);
        const out = method === STORED ? Buffer.from(raw) : inflateRawSync(raw, { maxOutputLength: declaredSize });
        if (out.length !== declaredSize) {
          throw new Error(
            `${name}: inflated to ${out.length} bytes, directory declared ${declaredSize}`
          );
        }
        return out;
      }
    });
  }
  return entries;
}

// ../engine-typescript/src/bundle.ts
var MAX_ENTRY_BYTES = 8 * 1024 * 1024;
var MAX_TOTAL_BYTES = 64 * 1024 * 1024;
var ALLOWED_SUFFIXES = /* @__PURE__ */ new Set([
  "",
  ".md",
  ".txt",
  ".json",
  ".jsonl",
  ".yaml",
  ".yml",
  ".toml",
  ".csv",
  ".tsv",
  ".cfg",
  ".ini",
  ".xml",
  ".html",
  ".htm",
  ".sql",
  ".env",
  ".sh",
  ".py",
  ".js",
  ".mjs",
  ".cjs",
  ".ts",
  ".rb",
  ".pl",
  ".lua",
  ".ps1",
  ".bat",
  ".svg",
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".webp",
  ".pdf"
]);
function suffixOf(name) {
  const base = name.slice(name.lastIndexOf("/") + 1);
  const dot = base.lastIndexOf(".");
  return dot <= 0 ? "" : base.slice(dot).toLowerCase();
}
async function extractBundle(archive, destination) {
  const staging = `${destination}.incoming-${process.pid}-${Math.random().toString(16).slice(2, 10)}`;
  const root = resolve(staging);
  let total = 0;
  try {
    await mkdir(staging, { recursive: true });
    for (const entry of readZipEntries(archive)) {
      const target = resolve(root, entry.name);
      const inside = relative(root, target);
      if (inside.startsWith("..") || isAbsolute(inside)) {
        throw new Error(`unsafe zip path: ${entry.name}`);
      }
      if (!ALLOWED_SUFFIXES.has(suffixOf(entry.name))) continue;
      if (entry.declaredSize > MAX_ENTRY_BYTES) continue;
      if (total + entry.declaredSize > MAX_TOTAL_BYTES) {
        throw new Error("zip uncompressed total too large");
      }
      const data = entry.read();
      total += data.length;
      await mkdir(join3(target, ".."), { recursive: true });
      await writeFile(target, data);
    }
    try {
      await rename(staging, destination);
    } catch (error) {
      const { access } = await import("node:fs/promises");
      await access(destination).catch(() => {
        throw error;
      });
      await rm(staging, { recursive: true, force: true });
    }
  } catch (error) {
    await rm(staging, { recursive: true, force: true });
    throw error;
  }
}
async function bundleRoot(destination) {
  let entries;
  try {
    entries = await readdir(destination, { withFileTypes: true });
  } catch {
    return destination;
  }
  const visible = entries.filter((entry) => !entry.name.startsWith("."));
  const only = visible[0];
  if (visible.length === 1 && only?.isDirectory()) return join3(destination, only.name);
  return destination;
}

// ../engine-typescript/src/relevance.ts
var STOP = /* @__PURE__ */ new Set([
  "a",
  "an",
  "the",
  "to",
  "for",
  "with",
  "using",
  "use",
  "create",
  "make",
  "help",
  "please",
  "and",
  "or",
  "of",
  "in",
  "on",
  "my",
  "me",
  "i",
  "want",
  "need",
  "how",
  "can",
  "from",
  "this",
  "that",
  "these",
  "those",
  "such",
  "no",
  "\u5E2E\u6211",
  "\u8BF7",
  "\u4E00\u4E2A",
  "\u4E00\u4E0B",
  "\u5982\u4F55",
  "\u600E\u4E48",
  "\u4F7F\u7528",
  "\u9700\u8981",
  "\u60F3\u8981",
  "\u8FDB\u884C"
]);
var ALIASES = {
  k8s: ["kubernetes"],
  pr: ["pull", "request"],
  ppt: ["powerpoint"],
  pptx: ["powerpoint"],
  postgres: ["postgresql"],
  transcription: ["transcribe"]
};
var GENERIC = /* @__PURE__ */ new Set([
  "extract",
  "review",
  "deploy",
  "deployment",
  "generate",
  "generator",
  "analysis",
  "optimize",
  "optimization",
  "process",
  "processing",
  "data",
  "code",
  "task"
]);
function queryTerms(query) {
  const chunks = query.toLowerCase().match(/[a-z0-9+#.-]+|[\p{Script=Han}]+/gu) ?? [];
  const raw = chunks.flatMap((chunk) => {
    if (!/^[\p{Script=Han}]+$/u.test(chunk) || chunk.length < 2) return [chunk];
    return Array.from({ length: chunk.length - 1 }, (_, index) => chunk.slice(index, index + 2));
  });
  const terms = [];
  for (const token of raw) {
    if (STOP.has(token) || token.length < 2) continue;
    const normalized = token.replace(/^[.-]+|[.-]+$/g, "");
    const expanded = ALIASES[normalized] ?? [stem(normalized)];
    for (const term of expanded) {
      if (term && !STOP.has(term) && !terms.includes(term)) terms.push(term);
    }
  }
  return terms;
}
function checkKeywordRelevance(query, hit) {
  const terms = queryTerms(query);
  if (terms.length === 0) {
    return { passed: false, matchedTerms: [], requiredMatched: false, matchRatio: 0 };
  }
  const tags = Array.isArray(hit.meta.tags) ? hit.meta.tags.join(" ") : "";
  const haystack = `${hit.name} ${String(hit.meta.description ?? "")} ${tags}`.toLowerCase();
  const matched = terms.filter((term) => containsTerm(haystack, term));
  const required = terms.filter((term) => !GENERIC.has(term));
  const requiredMatched = required.length === 0 || required.some((term) => matched.includes(term));
  const minimum = terms.length >= 4 ? 2 : 1;
  return {
    passed: requiredMatched && matched.length >= minimum,
    matchedTerms: matched,
    requiredMatched,
    matchRatio: matched.length / terms.length
  };
}
function stem(token) {
  if (/[+#.-]/.test(token)) return token;
  if (token.endsWith("ies") && token.length > 4) return `${token.slice(0, -3)}y`;
  if (token.endsWith("ing") && token.length > 5) return token.slice(0, -3);
  if (token.endsWith("ed") && token.length > 4) return token.slice(0, -2);
  if (token.endsWith("s") && token.length > 4 && !/(ss|us|is|es)$/.test(token)) {
    return token.slice(0, -1);
  }
  return token;
}
function containsTerm(text2, term) {
  if (/^[a-z0-9+#.-]+$/.test(term)) {
    const special = "\\^$.*+?()[]{}|";
    const escaped = Array.from(term, (char) => special.includes(char) ? `\\\\${char}` : char).join("");
    return new RegExp(`(^|[^a-z0-9])${escaped}([^a-z0-9]|$)`, "i").test(text2);
  }
  return text2.includes(term);
}

// ../engine-typescript/src/hub-source.ts
var OK_TOKENS = /* @__PURE__ */ new Set(["ok", "success"]);
var SkillHubClient = class {
  base;
  apiKey;
  timeoutMs;
  downloadTimeoutMs;
  cacheDir;
  source;
  constructor(endpoint, options = {}) {
    this.base = endpoint.replace(/\/+$/, "");
    this.apiKey = options.apiKey;
    this.timeoutMs = options.timeoutMs ?? 2e3;
    this.downloadTimeoutMs = options.downloadTimeoutMs ?? 3e4;
    this.cacheDir = options.cacheDir;
    this.source = options.source ?? "cli";
  }
  /**
   * Download a skill's bundle and extract it, or reuse an extracted copy.
   *
   * @param id - the catalog's own id for the skill.
   * @param meta - the skill's record, when the caller already fetched it;
   *   `slug` and `version` from it form the cache key.
   * @param signal - aborts the download when the turn is cancelled.
   * @returns the directory the skill's own paths resolve against, and the
   *   body the catalog stores, when the record carried one.
   * @throws Error when no cache directory is configured, or the archive is
   *   unusable. The caller keeps the unresolved body either way.
   */
  async install(id, meta, signal) {
    if (!this.cacheDir) throw new Error("no cache directory is configured for bundles");
    const record = meta ?? await this.get(id, signal);
    const slug = String(record.slug ?? record.skill_id ?? id).replace(/\//g, "_");
    const version = String(record.version ?? "v0");
    const destination = join4(this.cacheDir, `${slug}@${version}`);
    if (!existsSync2(destination)) {
      const archive = await this.download(id, signal);
      await extractBundle(archive, destination);
    }
    return {
      dir: await bundleRoot(destination),
      skillMd: typeof record.skill_md === "string" ? record.skill_md : ""
    };
  }
  /**
   * Fetch one bundle's bytes.
   *
   * @param id - the catalog's own id for the skill.
   * @param signal - aborts the request when the turn is cancelled.
   * @returns the archive.
   */
  async download(id, signal) {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      controller.abort();
    }, this.downloadTimeoutMs);
    const onAbort = () => {
      controller.abort();
    };
    signal?.addEventListener("abort", onAbort, { once: true });
    try {
      const headers = {};
      if (this.apiKey) headers.Authorization = `Bearer ${this.apiKey}`;
      const response = await fetch(this.downloadUrl(id), { headers, signal: controller.signal });
      if (!response.ok) throw new Error(`catalog returned HTTP ${response.status} for a bundle`);
      return Buffer.from(await response.arrayBuffer());
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    }
  }
  /**
   * Search the catalog. Metadata only — no bodies.
   * @param query - the search text, sent as `q`.
   * @param signal - aborts the request when the turn is cancelled.
   * @param limit - how many entries to ask the catalog for. Sent explicitly:
   *   the catalog's own default page may be smaller than the fan-out wants.
   * @returns the entries the catalog matched, in its own order.
   */
  async search(query, signal, limit = 20) {
    const url = `${this.base}/openapi/v1/skills?q=${encodeURIComponent(query)}&limit=${Math.max(1, Math.floor(limit))}`;
    const result = await this.getJson(url, signal);
    const items = result.items;
    return Array.isArray(items) ? items : [];
  }
  /**
   * Fetch one skill's full record.
   * @param id - the catalog's own id for the skill.
   * @param signal - aborts the request when the turn is cancelled.
   * @returns the record, including `skill_md` when the catalog carries it.
   */
  async get(id, signal) {
    const url = `${this.base}/openapi/v1/skills/${encodeURIComponent(id)}`;
    return await this.getJson(url, signal);
  }
  /**
   * Build the bundle URL for a caller that will download and extract it.
   * @param id - the catalog's own id for the skill.
   * @returns the download URL, tagged with this client's `source`.
   */
  downloadUrl(id) {
    return `${this.base}/openapi/v1/skills/${encodeURIComponent(id)}/download?source=${this.source}`;
  }
  async getJson(url, signal) {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      controller.abort();
    }, this.timeoutMs);
    const onAbort = () => {
      controller.abort();
    };
    signal?.addEventListener("abort", onAbort, { once: true });
    try {
      const headers = { "X-Request-ID": randomId() };
      if (this.apiKey) headers.Authorization = `Bearer ${this.apiKey}`;
      const res = await fetch(url, { headers, signal: controller.signal });
      if (!res.ok) throw new Error(`catalog returned HTTP ${res.status}`);
      const envelope = await res.json();
      if (envelope.status !== 0 || !OK_TOKENS.has(envelope.error ?? "")) {
        throw new Error(`catalog error ${envelope.error ?? "unknown"} (status ${envelope.status})`);
      }
      return envelope.result ?? {};
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    }
  }
};
var HubSkillSource = class {
  name = "hub";
  weight;
  client;
  minSafety;
  minQuality;
  maxCandidates;
  constructor(client, options = {}) {
    this.client = client;
    this.weight = options.weight ?? 0.85;
    this.minSafety = options.minSafety ?? 0.7;
    this.minQuality = options.minQuality ?? 0.45;
    this.maxCandidates = options.maxCandidates ?? 2;
  }
  async search(query, options, k) {
    const limit = Math.min(k, this.maxCandidates);
    if (limit <= 0) return [];
    const items = await this.client.search(query, options.signal, Math.max(limit * 4, limit));
    const hits = [];
    for (const item of items) {
      const id = item.id;
      const name = item.name;
      if (!id || !name) continue;
      if (item.score_safety !== void 0 && item.score_safety < this.minSafety) continue;
      if (item.quality_score !== void 0 && item.quality_score < this.minQuality) continue;
      const candidate = {
        qualifiedId: `hub/${id}`,
        name,
        content: "",
        score: item.quality_score ?? 0,
        meta: {
          source: "hub",
          id,
          skillId: item.skill_id,
          description: item.description,
          category: item.category,
          qualityScore: item.quality_score,
          installCount: item.install_count,
          tags: item.tags
        }
      };
      const relevance = checkKeywordRelevance(query, candidate);
      if (!relevance.passed) continue;
      hits.push({ ...candidate, meta: { ...candidate.meta, keywordRelevance: relevance } });
      if (hits.length >= limit) break;
    }
    return hits;
  }
};
function randomId() {
  return Array.from(
    { length: 4 },
    () => Math.floor(Math.random() * 4294967295).toString(16).padStart(8, "0")
  ).join("");
}

// ../engine-typescript/src/marketplace-source.ts
import { existsSync as existsSync3 } from "node:fs";
import { readFile, rm as rm2 } from "node:fs/promises";
import { join as join5 } from "node:path";
var MarketplaceClient = class {
  kind;
  base;
  cacheDir;
  timeoutMs;
  downloadTimeoutMs;
  constructor(kind, endpoint, options) {
    this.kind = kind;
    this.base = endpoint.replace(/\/+$/, "");
    this.cacheDir = options.cacheDir;
    this.timeoutMs = options.timeoutMs ?? 5e3;
    this.downloadTimeoutMs = options.downloadTimeoutMs ?? 3e4;
  }
  async search(query, signal, limit = 2) {
    return this.kind === "clawhub" ? this.searchClawHub(query, signal, limit) : this.searchSkillHubCn(query, signal, limit);
  }
  async install(hit, signal) {
    const slug = String(hit.meta.slug ?? hit.meta.id);
    const owner = String(hit.meta.owner ?? "");
    const version = String(hit.meta.version ?? "v0");
    const key = `${this.kind}-${owner ? `${owner}_` : ""}${slug}@${version}`.replace(/[^A-Za-z0-9_.@-]+/g, "_");
    const destination = join5(this.cacheDir, key);
    if (!existsSync3(destination)) {
      let archive;
      try {
        archive = await this.download(slug, owner, version, signal);
      } catch (error) {
        throw new Error(`download failed: ${errorMessage2(error)}`, { cause: error });
      }
      try {
        await extractBundle(archive, destination);
      } catch (error) {
        throw new Error(`extract failed: ${errorMessage2(error)}`, { cause: error });
      }
    }
    try {
      const dir = await bundleRoot(destination);
      const skillMd = await readFile(join5(dir, "SKILL.md"), "utf8");
      return { dir, body: stripFrontmatter(skillMd) };
    } catch (error) {
      await rm2(destination, { recursive: true, force: true }).catch(() => {
      });
      throw new Error(`read skill failed: ${errorMessage2(error)}`, { cause: error });
    }
  }
  async searchClawHub(query, signal, limit) {
    const url = new URL(`${this.base}/api/v1/search`);
    url.searchParams.set("q", query);
    url.searchParams.set("limit", String(limit));
    url.searchParams.set("nonSuspiciousOnly", "true");
    const payload = await this.json(url, signal);
    return (payload.results ?? []).flatMap((raw) => {
      const slug = String(raw.slug ?? "");
      const native = raw.native;
      const skill = native?.skill;
      const trust = raw.trust;
      if (!slug || trust?.visibility === "blocked" || trust?.installability === "blocked") return [];
      return [{
        id: String(raw.id ?? slug),
        slug,
        name: String(raw.displayName ?? slug),
        description: String(raw.summary ?? skill?.summary ?? ""),
        score: Number(raw.score ?? 0),
        owner: String(raw.ownerHandle ?? ""),
        version: String(raw.version ?? skill?.latestVersionId ?? "v0"),
        suspicious: skill?.isSuspicious === true,
        installable: trust?.installability == null || trust.installability === "installable",
        tags: Array.isArray(skill?.topics) ? skill.topics.map(String) : []
      }];
    });
  }
  async searchSkillHubCn(query, signal, limit) {
    const url = new URL(`${this.base}/api/skills`);
    url.searchParams.set("keyword", query);
    url.searchParams.set("sortBy", "score");
    url.searchParams.set("order", "desc");
    url.searchParams.set("page", "1");
    url.searchParams.set("pageSize", String(limit));
    const payload = await this.json(url, signal);
    if (payload.code !== 0) throw new Error("skillhub.cn search failed");
    return (payload.data?.skills ?? []).flatMap((raw) => {
      const slug = String(raw.slug ?? "");
      if (!slug || malicious(raw.securityReports)) return [];
      const namespace = raw.namespace;
      return [{
        id: String(namespace?.canonicalName ?? slug),
        slug,
        name: String(raw.name ?? slug),
        description: String(raw.description_zh ?? raw.description ?? ""),
        score: Number(raw.score ?? 0),
        owner: String(raw.ownerName ?? namespace?.handle ?? ""),
        version: String(raw.version ?? "v0"),
        installable: true
      }];
    });
  }
  async download(slug, owner, version, signal) {
    const url = new URL(`${this.base}/api/v1/download`);
    url.searchParams.set("slug", slug);
    if (this.kind === "clawhub" && owner) url.searchParams.set("ownerHandle", owner);
    if (this.kind === "skillhub_cn" && version !== "v0") url.searchParams.set("version", version);
    url.searchParams.set("source", this.kind === "skillhub_cn" ? "dsh" : "cli");
    return this.bytes(url, signal, this.downloadTimeoutMs);
  }
  async json(url, signal) {
    const bytes = await this.bytes(url, signal, this.timeoutMs);
    return JSON.parse(bytes.toString("utf8"));
  }
  async bytes(url, signal, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const abort = () => controller.abort();
    if (signal?.aborted) controller.abort();
    signal?.addEventListener("abort", abort, { once: true });
    try {
      const response = await fetch(url, { signal: controller.signal });
      if (!response.ok) throw new Error(`${this.kind} returned HTTP ${response.status}`);
      return Buffer.from(await response.arrayBuffer());
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
    }
  }
};
var MarketplaceSkillSource = class {
  constructor(client, options = {}) {
    this.client = client;
    this.name = client.kind;
    this.weight = options.weight ?? 0.75;
  }
  client;
  name;
  weight;
  async search(query, options, k) {
    const items = await this.client.search(query, options.signal, Math.min(2, k));
    return items.filter((item) => !item.suspicious && item.installable !== false).slice(0, Math.min(2, k)).map((item) => ({
      qualifiedId: `${this.name}/${item.id}`,
      name: item.name,
      content: "",
      score: item.score,
      meta: {
        source: this.name,
        id: item.id,
        slug: item.slug,
        owner: item.owner,
        version: item.version,
        description: item.description,
        tags: item.tags
      }
    }));
  }
};
function malicious(value) {
  if (!value || typeof value !== "object") return false;
  return Object.values(value).some((report) => report && typeof report === "object" && ["malicious", "suspicious"].includes(String(report.status)));
}
function stripFrontmatter(text2) {
  if (!text2.startsWith("---")) return text2;
  const end = text2.indexOf("\n---", 3);
  return end < 0 ? text2 : text2.slice(end + 4).replace(/^\n+/, "");
}
function errorMessage2(error) {
  return error instanceof Error ? error.message : String(error);
}

// src/cached-local-source.ts
import { mkdirSync, readFileSync as readFileSync2, readdirSync, renameSync, statSync as statSync2, writeFileSync } from "node:fs";
import { dirname as dirname2, join as join7 } from "node:path";

// ../engine-typescript/src/local-source.ts
import { readFile as readFile2, readdir as readdir2 } from "node:fs/promises";
import { basename, join as join6 } from "node:path";

// ../engine-typescript/src/bm25.ts
var TOKEN_RE = /[a-z0-9]{2,}|[一-鿿]+/g;
var CJK_RE = /^[一-鿿]/;
function tokenize(text2) {
  const out = [];
  for (const run of text2.toLowerCase().match(TOKEN_RE) ?? []) {
    if (!CJK_RE.test(run)) {
      out.push(run);
      continue;
    }
    if (run.length === 1) out.push(run);
    else for (let i = 0; i < run.length - 1; i += 1) out.push(run.slice(i, i + 2));
  }
  return out;
}
var STOPWORD_DF_RATIO = 0.5;
var STOPWORD_MIN_CORPUS = 10;
var BM25Okapi = class {
  k1;
  b;
  corpusSize;
  avgdl;
  /** Per document, its term frequencies and its length, kept together. */
  docs;
  idf;
  /**
   * Terms this corpus cannot distinguish on.
   *
   * A word in over half the documents carries no ranking signal here — in
   * a skills directory that is "skill", "run", "use", the vocabulary of
   * the format itself — but its idf stays just above zero, so every
   * document holding it still collects score and an unrelated query still
   * produces a confident-looking ranked list.
   *
   * Below `STOPWORD_MIN_CORPUS` documents this stays empty: on a corpus of
   * three, a term in two is over the threshold, and pruning the query down
   * to nothing is a worse answer than a weak ranking.
   */
  stopwords;
  constructor(tokenizedCorpus, k1 = 1.5, b = 0.75) {
    this.k1 = k1;
    this.b = b;
    this.corpusSize = tokenizedCorpus.length;
    this.avgdl = this.corpusSize ? tokenizedCorpus.reduce((a, d) => a + d.length, 0) / this.corpusSize : 0;
    this.docs = [];
    const df = /* @__PURE__ */ new Map();
    for (const doc of tokenizedCorpus) {
      const freqs = /* @__PURE__ */ new Map();
      for (const tok of doc) freqs.set(tok, (freqs.get(tok) ?? 0) + 1);
      this.docs.push({ freqs, len: doc.length });
      for (const tok of freqs.keys()) df.set(tok, (df.get(tok) ?? 0) + 1);
    }
    const n = this.corpusSize;
    this.idf = /* @__PURE__ */ new Map();
    const stopwords = /* @__PURE__ */ new Set();
    for (const [term, count] of df) {
      this.idf.set(term, Math.log(1 + (n - count + 0.5) / (count + 0.5)));
      if (n >= STOPWORD_MIN_CORPUS && count / n > STOPWORD_DF_RATIO) stopwords.add(term);
    }
    this.stopwords = stopwords;
  }
  /** Score every document against the query. Index-aligned with the corpus. */
  /**
   * Score every document in the corpus against one query.
   * @param queryTokens - the tokenized query, from `tokenize`.
   * @returns one score per document, in corpus order; 0 where nothing matched.
   */
  getScores(queryTokens) {
    const scores = new Array(this.corpusSize).fill(0);
    if (queryTokens.length === 0 || this.corpusSize === 0) return scores;
    for (const term of queryTokens) {
      if (this.stopwords.has(term)) continue;
      const idf = this.idf.get(term) ?? 0;
      if (idf <= 0) continue;
      for (const [i, doc] of this.docs.entries()) {
        const f = doc.freqs.get(term) ?? 0;
        if (f === 0) continue;
        const norm = this.k1 * (1 - this.b + this.b * doc.len / (this.avgdl || 1));
        scores[i] = (scores[i] ?? 0) + idf * f * (this.k1 + 1) / (f + norm);
      }
    }
    return scores;
  }
};

// ../engine-typescript/src/local-source.ts
var SKILL_FILE = "SKILL.md";
var INDEXED_BODY_CHARS = 4e3;
var SKIP_DIRS = /* @__PURE__ */ new Set([".git", "__pycache__", "node_modules", ".venv", "venv"]);
var LocalSkillSource = class {
  name = "local";
  weight = 1;
  roots;
  maxDepth;
  indexBody;
  cache;
  index;
  constructor(roots, options = {}) {
    this.roots = roots;
    this.maxDepth = options.maxDepth ?? 5;
    this.indexBody = options.indexBody ?? false;
  }
  /** Drop the scan and the index. Call when a `SKILL.md` changes on disk. */
  invalidate() {
    this.cache = void 0;
    this.index = void 0;
  }
  async search(query, options, k) {
    const { bm25, skills } = await this.ensureIndex();
    if (skills.length === 0) return [];
    options.signal?.throwIfAborted();
    const scores = bm25.getScores(tokenize(query));
    return skills.map((skill, i) => ({ score: scores[i] ?? 0, skill })).filter((entry) => entry.score > 0).sort((a, b) => b.score - a.score).slice(0, k).map(({ score, skill }) => ({
      qualifiedId: `local/${skill.name}`,
      name: skill.name,
      content: skill.content,
      score,
      meta: {
        source: "local",
        description: skill.description,
        // The renderer turns this into an absolute path the model can hand
        // to a file tool; without it a body saying `scripts/x.sh` resolves
        // against the agent's cwd, which is the wrong directory.
        skillDir: skill.dir
      }
    }));
  }
  async ensureIndex() {
    if (this.index) return this.index;
    const skills = await this.listAll();
    const corpus = skills.map((s) => tokenize(formatSkillText(s, this.indexBody)));
    this.index = { bm25: new BM25Okapi(corpus), skills };
    return this.index;
  }
  /**
   * Scan every root once and cache the result.
   * @returns every skill found, first root winning a name collision.
   */
  async listAll() {
    if (this.cache) return this.cache;
    const found = [];
    const seen = /* @__PURE__ */ new Set();
    for (const root of this.roots) {
      for await (const file of walk(root.path, this.maxDepth)) {
        let text2;
        try {
          text2 = await readFile2(file, "utf8");
        } catch {
          continue;
        }
        const { meta, body } = parseFrontmatter(text2);
        const dir = file.slice(0, file.length - SKILL_FILE.length - 1);
        const name = meta.name ?? basename(dir);
        const key = `${root.name}/${name}`;
        if (seen.has(key)) continue;
        seen.add(key);
        found.push({
          name,
          description: meta.description ?? "",
          content: body,
          source: root.name,
          dir
        });
      }
    }
    this.cache = found;
    return found;
  }
};
function formatSkillText(skill, indexBody = false) {
  const parts = [skill.name, skill.name, skill.description];
  if (indexBody) parts.push(skill.content.slice(0, INDEXED_BODY_CHARS));
  return parts.join(" ");
}
async function* walk(root, maxDepth) {
  const stack = [{ dir: root, depth: 0 }];
  for (let next = stack.pop(); next !== void 0; next = stack.pop()) {
    const { dir, depth } = next;
    if (depth > maxDepth) continue;
    let entries;
    try {
      entries = await readdir2(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (entry.isDirectory()) {
        if (!SKIP_DIRS.has(entry.name)) stack.push({ dir: join6(dir, entry.name), depth: depth + 1 });
      } else if (entry.name === SKILL_FILE) {
        yield join6(dir, entry.name);
      }
    }
  }
}
function parseFrontmatter(text2) {
  if (!text2.startsWith("---")) return { meta: {}, body: text2 };
  const end = text2.indexOf("\n---", 3);
  if (end === -1) return { meta: {}, body: text2 };
  const head = text2.slice(3, end);
  const body = text2.slice(end + 4).replace(/^\n+/, "");
  const meta = {};
  for (const line of head.split("\n")) {
    const colon = line.indexOf(":");
    if (colon === -1) continue;
    const key = line.slice(0, colon);
    if (key.startsWith(" ") || key.startsWith("	") || key.startsWith("#")) continue;
    meta[key.trim()] = line.slice(colon + 1).trim().replace(/^["']|["']$/g, "");
  }
  return { meta, body };
}

// src/cached-local-source.ts
var SKIP_DIRS2 = /* @__PURE__ */ new Set([".git", "__pycache__", "node_modules", ".venv", "venv"]);
var SKILL_FILE2 = "SKILL.md";
var CachedLocalSkillSource = class extends LocalSkillSource {
  cachePath;
  rootPaths;
  depth;
  constructor(roots, options) {
    super(roots, options);
    this.cachePath = options.cachePath;
    this.rootPaths = roots.map((root) => root.path);
    this.depth = options.maxDepth ?? 5;
  }
  /**
   * The parent's scan, served from disk when nothing on disk has changed.
   * @returns every skill found, first root winning a name collision.
   */
  async listAll() {
    if (!this.cachePath) return super.listAll();
    const fingerprint = this.fingerprint();
    const cached = this.read();
    if (cached && cached.fingerprint === fingerprint) return cached.skills;
    const skills = await super.listAll();
    this.write({ version: 1, fingerprint, skills });
    return skills;
  }
  /** Path and mtime of every `SKILL.md` under the roots, in scan order. */
  fingerprint() {
    const parts = [];
    for (const root of this.rootPaths) collect(root, this.depth, parts);
    return `${parts.length}|${hash(parts.join("\n"))}`;
  }
  read() {
    try {
      const parsed = JSON.parse(readFileSync2(this.cachePath, "utf8"));
      if (!parsed || typeof parsed !== "object") return void 0;
      const file = parsed;
      if (file.version !== 1 || typeof file.fingerprint !== "string") return void 0;
      return Array.isArray(file.skills) ? file : void 0;
    } catch {
      return void 0;
    }
  }
  write(file) {
    try {
      mkdirSync(dirname2(this.cachePath), { recursive: true });
      const temp = `${this.cachePath}.${process.pid}.tmp`;
      writeFileSync(temp, JSON.stringify(file));
      renameSync(temp, this.cachePath);
    } catch {
    }
  }
};
function collect(dir, depth, out) {
  if (depth < 0) return;
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    if (SKIP_DIRS2.has(entry.name)) continue;
    const path = join7(dir, entry.name);
    if (entry.isDirectory()) collect(path, depth - 1, out);
    else if (entry.name === SKILL_FILE2) {
      try {
        out.push(`${path}:${statSync2(path).mtimeMs}`);
      } catch {
      }
    }
  }
}
function hash(text2) {
  let value = 5381;
  for (let index = 0; index < text2.length; index += 1) {
    value = (value * 33 ^ text2.charCodeAt(index)) >>> 0;
  }
  return value.toString(36);
}

// src/model.ts
function createChatModel(options) {
  if (!options.model) return void 0;
  const base = options.baseUrl.replace(/\/+$/, "");
  return {
    async complete(prompt, opts) {
      const headers = { "Content-Type": "application/json" };
      if (options.apiKey) headers.Authorization = `Bearer ${options.apiKey}`;
      const response = await fetch(`${base}/chat/completions`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          model: options.model,
          messages: [{ role: "user", content: prompt }],
          temperature: 0
        }),
        ...opts.signal ? { signal: opts.signal } : {}
      });
      if (!response.ok) {
        const detail = await response.text().catch(() => "");
        throw new Error(`model endpoint returned HTTP ${response.status}: ${detail.slice(0, 200)}`);
      }
      const body = await response.json();
      return body.choices?.[0]?.message?.content ?? "";
    }
  };
}

// src/retrieve.ts
function expandHome(path, home = homedir2()) {
  if (path === "~") return home;
  if (path.startsWith("~/")) return join8(home, path.slice(2));
  return path;
}
function buildEngine(config, onDiagnostic, workspaceDir) {
  const sources = [];
  const dirs = config.skillsDirs.map((dir) => expandHome(dir)).filter(Boolean);
  if (dirs.length > 0) {
    const local = new CachedLocalSkillSource(
      dirs.map((path) => ({ path, name: "local" })),
      { indexBody: config.indexBody, cachePath: expandHome(config.indexCachePath) }
    );
    local.weight = config.localWeight;
    sources.push(local);
  }
  let client;
  if (config.hubEndpoint) {
    client = new SkillHubClient(config.hubEndpoint, {
      ...config.hubApiKey ? { apiKey: config.hubApiKey } : {},
      // Outside every scanned directory. `~/.workbuddy-ai/plugins/cache` is
      // one of the defaults, so a bundle extracted under it would come back
      // as a local skill on the next scan.
      cacheDir: expandHome(config.bundleCacheDir) || join8(homedir2(), ".workbuddy-ai", "skillsearch-bundles")
    });
    const hub = new HubSkillSource(client);
    hub.weight = config.hubWeight;
    sources.push(hub);
  }
  const marketplaceClients = /* @__PURE__ */ new Map();
  for (const [kind, endpoint] of [
    ["clawhub", config.clawhubEndpoint],
    ["skillhub_cn", config.skillhubCnEndpoint]
  ]) {
    if (!endpoint) continue;
    const marketplace = new MarketplaceClient(kind, endpoint, {
      cacheDir: expandHome(config.bundleCacheDir) || join8(homedir2(), ".workbuddy-ai", "skillsearch-bundles"),
      // ClawHub measured 4–5s on the supported route. Give search headroom,
      // but leave time under the hook's global deadline for body hydration.
      timeoutMs: Math.max(1, Math.min(config.timeoutMs, 6500)),
      downloadTimeoutMs: Math.max(1, config.timeoutMs)
    });
    marketplaceClients.set(kind, marketplace);
    sources.push(new MarketplaceSkillSource(marketplace));
  }
  const model = createChatModel({
    baseUrl: config.modelBaseUrl,
    apiKey: config.modelApiKey,
    model: config.model
  });
  return new SkillSearchEngine(
    {
      sources,
      ...onDiagnostic ? { onDiagnostic } : {},
      ...model && config.rewrite ? { rewriter: new QueryRewriter(model) } : {},
      ...model && (config.gate ?? (Boolean(config.hubEndpoint) || marketplaceClients.size > 0)) ? { gate: new LLMGateFilter(model, { maxSelect: config.maxSelect }) } : {},
      ...client || marketplaceClients.size > 0 ? {
        fetchBody: async (hit, signal) => {
          const marketplace = marketplaceClients.get(String(hit.meta.source));
          if (marketplace) {
            const installed = await marketplace.install(hit, signal);
            return { body: installed.body, record: { _installed: installed } };
          }
          if (hit.meta.source !== "hub" || !client) return void 0;
          const record = await client.get(String(hit.meta.id), signal);
          return {
            ...typeof record.skill_md === "string" ? { body: record.skill_md } : {},
            record
          };
        },
        materialise: async (hit, signal) => {
          const marketplace = marketplaceClients.get(String(hit.meta.source));
          if (marketplace) {
            const fetched2 = hit.meta._fetched;
            const installed2 = fetched2?._installed ?? await marketplace.install(hit, signal);
            return { dir: installed2.dir, body: installed2.body };
          }
          if (hit.meta.source !== "hub" || !client) return void 0;
          const fetched = hit.meta._fetched;
          const installed = await client.install(String(hit.meta.id), fetched, signal);
          return { dir: installed.dir, body: installed.skillMd };
        }
      } : {}
    },
    {
      topK: config.topK,
      gatePool: config.gatePool,
      rrfK: config.rrfK,
      // PathGuard placeholders' per-agent facts. WorkBuddy's own config root
      // is ~/.workbuddy-ai; the agent's writable output is its workspace,
      // falling back to the hook process's cwd when the payload reports none.
      outputDir: workspaceDir || process.cwd(),
      homeDir: homedir2(),
      stateDir: join8(homedir2(), ".workbuddy-ai"),
      resolvePlaceholders: config.resolvePlaceholders
    }
  );
}
async function retrieveForTurn(query, config, deps = {}, onDiagnostic, workspaceDir) {
  if (!query.trim()) return "";
  let engine;
  try {
    engine = (deps.buildEngineFn ?? buildEngine)(config, onDiagnostic, workspaceDir);
  } catch {
    return "";
  }
  if (!engine.enabled) return "";
  const controller = new AbortController();
  const timer = setTimeout(() => {
    controller.abort();
  }, config.timeoutMs);
  try {
    return await engine.retrieve(query, {
      signal: controller.signal,
      ...config.availableTools.length > 0 ? { availableTools: config.availableTools } : {}
    });
  } catch {
    return "";
  } finally {
    clearTimeout(timer);
  }
}

// src/mcp.ts
var PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05", "2024-10-07"];
var SKILL_SEARCH_DESCRIPTION = [
  "Search the skill library for a procedure that fits the task at hand, and",
  "get back the matching skills in full.",
  "",
  "A skill is a written workflow for a specific job \u2014 filling PDF forms,",
  "building a slide deck, migrating a schema \u2014 including the exact commands,",
  "files, and in-house conventions it needs.",
  "",
  "Reach for it when:",
  "- a task needs a multi-step procedure you would otherwise improvise;",
  "- a task names a format, tool, or workflow you would have to guess at;",
  "- a question asks about an internal convention, template, standard, or",
  '  "our" way of doing something \u2014 a skill is where those are written down,',
  "  so searching here comes before answering that you do not know.",
  "",
  "Search with the words the task actually uses; the query is matched against",
  "skill names and descriptions. Returns nothing when the library has no fit,",
  "which is a normal answer and means: proceed on your own."
].join("\n");
var SKILL_SEARCH_TOOL = {
  name: "skill_search",
  description: SKILL_SEARCH_DESCRIPTION,
  inputSchema: {
    type: "object",
    properties: {
      query: {
        type: "string",
        description: `What you need to do, in the task's own words \u2014 e.g. "extract tables from a scanned PDF invoice".`
      }
    },
    required: ["query"],
    additionalProperties: false
  }
};
function text(value) {
  return { content: [{ type: "text", text: value }] };
}
async function handle(message, config, search = (query) => retrieveForTurn(query, config)) {
  const { id, method } = message;
  if (id === void 0) return void 0;
  const reply = (result) => ({ jsonrpc: "2.0", id, result });
  if (method === "initialize") {
    const asked = String(message.params?.protocolVersion ?? "");
    return reply({
      protocolVersion: PROTOCOL_VERSIONS.includes(asked) ? asked : PROTOCOL_VERSIONS[0],
      capabilities: { tools: {} },
      serverInfo: { name: "skillsearch", version: "0.2.0" }
    });
  }
  if (method === "tools/list") {
    return reply({ tools: config.mode === "on_demand" ? [SKILL_SEARCH_TOOL] : [] });
  }
  if (method === "ping") return reply({});
  if (method === "tools/call") {
    const params = message.params ?? {};
    if (config.mode !== "on_demand") {
      return {
        jsonrpc: "2.0",
        id,
        error: { code: -32601, message: "skill_search is not offered in auto mode" }
      };
    }
    if (params.name !== SKILL_SEARCH_TOOL.name) {
      return { jsonrpc: "2.0", id, error: { code: -32602, message: `Unknown tool: ${String(params.name)}` } };
    }
    const query = String(params.arguments?.query ?? "").trim();
    if (!query) return reply(text("skill_search needs a query describing the task."));
    const block = await search(query);
    return reply(text(block || `No skill in the library matches "${query}". Proceed without one.`));
  }
  return { jsonrpc: "2.0", id, error: { code: -32601, message: `Method not found: ${String(method)}` } };
}
function serve(config) {
  const lines = createInterface({ input: process.stdin });
  lines.on("line", (line) => {
    if (!line.trim()) return;
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      return;
    }
    void handle(message, config).then((response) => {
      if (response) process.stdout.write(`${JSON.stringify(response)}
`);
    }).catch(() => {
      if (message.id !== void 0) {
        process.stdout.write(`${JSON.stringify({
          jsonrpc: "2.0",
          id: message.id,
          error: { code: -32603, message: "skill search failed" }
        })}
`);
      }
    });
  });
}
function main() {
  serve(loadConfig(readConfigDocument()));
}
if (argv[1] && fileURLToPath(import.meta.url) === argv[1]) main();
export {
  SKILL_SEARCH_DESCRIPTION,
  SKILL_SEARCH_TOOL,
  handle,
  serve
};
