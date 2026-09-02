# INSTALL.agent.md — installation playbook

You are an agent installing **skillsearch** — the SkillCorpus Plugins — into
the host you are running in. A human asked for this and will read your
report. Follow this file top to bottom.

## Rules you must follow throughout

1. **Show before you change.** Before writing or editing any config file,
   show the user the exact diff (or the full new file if it did not exist)
   and wait for their go-ahead unless they already told you to proceed.
2. **Back up first.** Before editing an existing config file, copy it to
   `<name>.bak-skillsearch` next to itself.
3. **Merge, never replace.** Add keys into existing JSON/TOML/YAML; do not
   rewrite whole files. If an existing config file fails to parse, **stop
   and tell the user** — do not overwrite it.
4. **Stop on ambiguity.** If host detection matches more than one host, ask
   the user which one to target instead of guessing.
5. **Finish with the verification section.** An install without a passing
   verification is not done — report exactly which step failed.

## Step 0 — detect the host

Run these checks. The first section whose check passes is your target; if
several pass, ask the user.

| Host | It is this host if… |
| --- | --- |
| WorkBuddy | `~/.workbuddy-ai/settings.json` exists, or your own hook payloads carry `"client": "WorkBuddy"` |
| Hermes | `$HERMES_HOME` is set, or `~/.hermes/` exists |
| OpenClaw | `~/.openclaw/openclaw.json` exists, or an `openclaw` process/CLI is present; run `openclaw --version` before choosing the 1.x or 2.0 instructions below |
| DeepSeek Harness | the workspace you are in has a `cordis.yml` and a `packages/` tree |
| Raven | `~/.raven/` exists, or `raven` CLI is present |

Note where this repository is checked out (clone it if the user gave you only
the URL), then change into its `skillcorpus_plugin/` directory. Every relative
path and command below starts there.

## WorkBuddy

WorkBuddy installs through its standard plugin marketplace. Its own playbook
covers discovery and restart verification: follow [`plugin-workbuddy/INSTALL.agent.md`](plugin-workbuddy/INSTALL.agent.md)
step by step — do not improvise a WorkBuddy install from this file. The
same rules apply there, plus two stricter ones it states: report each step
as you finish it, and never route around a failed step.

## Hermes

```bash
pip install ./engine-python
cp -r plugin-hermes "$HERMES_HOME/plugins/skillsearch"
```

Then create or merge `$HERMES_HOME/skillsearch.json` (show the diff first):

```json
{
  "skills_dir": "~/.hermes/skills",
  "model": ""
}
```

Tell the user to run `hermes memory setup` and pick `skillsearch` — the
provider occupies the memory slot, so mention that it replaces whatever
memory provider currently holds that slot, and let them decide.

A user who would rather not go through the interactive setup selects the
provider by hand, in `$HERMES_HOME/config.yaml` — a different file from the
`skillsearch.json` above, which carries only this plugin's own settings:

```yaml
memory:
  provider: skillsearch
```

## OpenClaw 1.x (through 2026.7.x)

```bash
npm install --prefix plugin-openclaw
npm run --prefix plugin-openclaw build      # produces dist/index.js
```

Merge two keys into `~/.openclaw/openclaw.json` (backup + diff first).
`load.paths` must be the **absolute** path of the `plugin-openclaw`
directory in this checkout:

```jsonc
{
  "plugins": {
    "load": { "paths": ["/ABS/PATH/TO/plugin-openclaw"] },
    "entries": {
      "skillsearch": {
        "enabled": true,
        "config": { "skillsDirs": ["~/.openclaw/skills"] }
      }
    }
  }
}
```

Works on OpenClaw releases from 2026.3.8 through 2026.7.x. The gateway/app must be
restarted to pick up a new plugin — tell the user, or do it if they said to
proceed.

## OpenClaw 2.0 (2026.8.1 and newer)

Do not install the 1.x package on OpenClaw 2.0. Build the separate context-engine
package instead:

```bash
npm install --prefix plugin-openclaw2
npm run --prefix plugin-openclaw2 build
```

For the default `on_demand` mode, merge the following into
`~/.openclaw/openclaw.json`. `load.paths` must be the absolute path of the
`plugin-openclaw2` directory:

```jsonc
{
  "plugins": {
    "load": { "paths": ["/ABS/PATH/TO/plugin-openclaw2"] },
    "entries": {
      "skillsearch": {
        "enabled": true,
        "config": {
          "mode": "on_demand",
          "skillsDirs": ["~/.openclaw/skills"]
        }
      }
    }
  }
}
```

In `on_demand`, the plugin registers `skill_search` and does **not** occupy the
context-engine slot. If the user explicitly chooses `mode: auto`, also add the
two grants to the `skillsearch` entry and select its context engine:

```jsonc
{
  "plugins": {
    "entries": {
      "skillsearch": {
        "hooks": {
          "allowConversationAccess": true,
          "allowPromptInjection": true
        },
        "config": { "mode": "auto" }
      }
    },
    "slots": { "contextEngine": "skillsearch" }
  }
}
```

The grants belong under `plugins.entries.skillsearch`, not in a top-level
`hooks` block. Restart the gateway/app after changing the package or config.

## DeepSeek Harness

Inside the harness workspace:

```bash
cp -r <repo>/engine-typescript packages/skill/skill-search
```

1. Add `{ "path": "./packages/skill/skill-search" }` to the `references` in
   `tsconfig.host.json` (backup + diff first).
2. Run `pnpm install`.
3. Add a row to `cordis.yml` (diff first):

```yaml
- id: skill-search
  name: '@deepseek-ai/dsh-skill-search'
  config:
    skillsDirs: ['.dsh/skills']
```

If `dsh-tool-skill` is mounted, tell the user the two publish the same
skills twice and ask whether to disable it — do not disable it yourself.

## Raven

```bash
pip install ./engine-python ./plugin-raven
```

The default `on_demand` mode registers `skill_search` and works today. Be
straight with the user that `mode: auto` still depends on Raven’s upstream
`context_segments` slot; until Raven ships it, auto injection cannot claim the
`skills` stage. Raven’s built-in retrieval keeps working. Configure nothing else
unless the user requests `auto`; then explain this limitation. Verify the
packages import:

```bash
python -c "import skillsearch, skillsearch_raven; print('import ok')"
```

## Network and optional model configuration

- **A model for the rewriter and gate**: better selection, two small model
  calls per retrieving turn. Ask the user which model/route to use; leave
  empty if they don't care.
- **Remote sources are enabled by default**: EverMind SkillHub
  (`https://skillhub.evermind.ai`), ClawHub, and skillhub.cn each receive the
  retrieval query and may download candidate skill content to disk. State this
  plainly during installation. The user can set any endpoint to an empty
  string to disable that source, or clear all three for local-only operation.

## Verification — definition of done

Do all of these; the install is done only when every box is ticked.

1. **Plugin discovered**: the host's own listing/log shows the plugin loaded
   (Hermes: provider list; OpenClaw 1.x/2.0: plugin log line; DSH: boot log;
   Raven: the import check and, when available, its tool list).
2. **Create a test skill** in the configured skills directory:

```bash
mkdir -p <skills_dir>/pdf-tables
printf -- '---\nname: pdf-tables\ndescription: Extract tables from PDF documents, scanned or native, into CSV.\n---\nUse camelot for native PDFs.\n' > <skills_dir>/pdf-tables/SKILL.md
```

3. **Positive probe** — and *which* probe depends on the mode, because the
   two modes deliver skills by different routes. Check the configured mode
   first; if nothing sets `mode`, it is `on_demand`.

   **`on_demand` (the default from 0.3.0)** — start a fresh session and ask
   *"how do I extract tables from a scanned PDF invoice into CSV?"*. Confirm
   the agent **called `skill_search`** and that the call returned
   `pdf-tables` (the host's tool-call display, or its debug view). There is
   no `# Skills` block in this mode and its absence is correct, not a
   failure. If the agent answers well without calling the tool, the probe
   has not passed — it answered from its own knowledge.

   **`auto`** — same question; confirm a `# Skills` block containing
   `pdf-tables` reached the context (the host's debug view, or
   `SKILLSEARCH_GATE_LOG_PATH` on Python hosts). Confirm no tool call
   happened; `skill_search` is not offered in this mode.

4. **Negative probe**: ask *"zxqv-7319, reply with this exact string only."*
   — in `auto`, confirm nothing was injected; in `on_demand`, confirm
   `skill_search` was not called. Do not use a weather question: the public
   catalogues contain real weather skills, so a hit there is correct
   behaviour and tells you nothing.
5. **Report**: list every file you created or edited (with the diffs), the
   mode you verified, the probe results, and how to undo everything (the
   section below).

If step 3 fails: check the skills directory path in the config matches
where you wrote the test skill; check the host was restarted after config
changes; on Python hosts set `SKILLSEARCH_GATE_LOG_PATH=/tmp/gate.jsonl`
and re-ask, then read the record to see whether retrieval found nothing or
the gate rejected it. In `on_demand`, also check the tool is actually on the
agent's tool surface before concluding the model chose not to call it.
Report what you find rather than retrying blindly.

A fuller set of prompts and acceptance conditions — six hosts, both modes,
failure isolation and upgrade — lives in
[`tests/host-e2e/cases.md`](tests/host-e2e/cases.md).

## Uninstall

When the user asks to remove skillsearch:

1. Remove what install added — Hermes: `$HERMES_HOME/plugins/skillsearch/`
   and the `skillsearch.json`; OpenClaw: the two config keys and the
   `plugin-openclaw` path entry; DSH: the `cordis.yml` row, the tsconfig
   reference, and `packages/skill/skill-search/`; OpenClaw 2.0: the
   `plugin-openclaw2` path plus any `contextEngine` slot and hook grants added
   for auto mode; Raven:
   `pip uninstall skillsearch skillsearch-raven`.
2. Offer to delete the bundle cache (`~/.skillsearch/hub`,
   `~/.openclaw/skillsearch-bundles`, or `~/.dsh/skillsearch-bundles`).
3. Restore or delete the `.bak-skillsearch` backups per the user's call.
4. Show the diffs, same rule as installing.
