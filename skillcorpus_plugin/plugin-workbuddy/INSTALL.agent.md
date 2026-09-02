# INSTALL.agent.md — WorkBuddy installation playbook

You are an agent installing **skillsearch** into WorkBuddy — the host you are
running in. A human asked for this and will read your report. Follow this
file top to bottom.

The repository-root `INSTALL.agent.md` routes WorkBuddy installs here.

## Rules you must follow throughout

1. **Show before you change.** Before writing or editing any config file,
   show the user the exact diff (or the full new file if it did not exist)
   and wait for their go-ahead unless they already told you to proceed.
2. **Back up first, with a timestamp.** Before editing an existing config
   file, copy it to `<name>.bak-skillsearch.<ISO timestamp>` next to
   itself — timestamped, so a second install never clobbers the first
   backup.
3. **Merge, never replace.** Add keys into existing JSON; do not rewrite
   whole files. If an existing config file fails to parse, **stop and tell
   the user** — do not overwrite it, do not "fix" it.
4. **Stop on ambiguity.** If host detection is unclear, ask instead of
   guessing. Values like the marketplace name and the version are **read
   from files, never invented** — each step below says where.
5. **Report each step, and never route around a failure.** After each
   numbered step, tell the user in one line what happened. If a step
   fails, stop and report it exactly — do not skip it, and do not invent
   an alternative path.
6. **Finish with the verification section.** An install without a passing
   verification is not done — report exactly which step failed.

## Step 0 — detect the host

| Host | It is this host if… |
| --- | --- |
| WorkBuddy | `~/.workbuddy-ai/settings.json` exists, or `WorkBuddy AI.app` is installed, or your own hook payloads carry `"client": "WorkBuddy"` |

Note where this repository is checked out (clone it if the user gave you only
the URL), then change into its `skillcorpus_plugin/` directory. Every relative
path and command below starts there.

## WorkBuddy

WorkBuddy discovers this repository through the root
`.codebuddy-plugin/marketplace.json`. Use the host's marketplace installer;
do not edit `settings.json`, `installed_plugins.json`, or
`known_marketplaces.json` by hand.

1. Open **Experts · Skills · Connectors → Skills → Plugin Marketplace**.
2. Add `EverMind-AI/SkillCorpus` as a marketplace source. A git URL or release
   zip works; do not use a local directory, which is not persistent across a
   restart on WorkBuddy 5.3.13.
3. Before installation, confirm `CODEBUDDY_DISABLE_EXTENDED_PLUGIN_HOOKS` is not
   `1` in the environment that launches WorkBuddy. If it is, extended plugin
   hooks are disabled globally: clear it from that launcher and fully restart
   WorkBuddy before continuing.
4. In the `skillcorpus` marketplace, install and enable **Skill Search**
   (`skillsearch`, version read from its plugin manifest).
5. Fully quit and reopen WorkBuddy.

If the marketplace or plugin is not discovered, stop and report the exact UI
error and host logs. Do not route around discovery by copying files into the
cache or editing WorkBuddy's internal JSON records.

## Network and optional model configuration

Configuration lives in
`~/.workbuddy-ai/plugins/data/skillsearch-<market>/config.json` (the
environment, set per-command in `hooks.json`, wins over it). The hook normally
reads `<market>` from its installed cache path; non-standard launchers may set
`SKILLSEARCH_MARKETPLACE`, or set `SKILLSEARCH_DATA_DIR` to override the whole
state directory. Existing marketplace installs continue using the name parsed
from their current path.

- **A model for the rewriter and gate**: better selection, two small model
  calls per retrieving turn — spent inside the silence between the user
  pressing enter and the reply starting, which this host does not indicate.
  Ask which route to use; leave empty if they don't care.
- **Remote sources are enabled by default**: EverMind SkillHub
  (`https://skillhub.evermind.ai`), ClawHub, and skillhub.cn each receive the
  retrieval query and may download candidate skill content to disk. State this
  plainly during installation. The user can set any endpoint to an empty
  string to disable that source, or clear all three for local-only operation.

## Verification — definition of done

This plugin has two modes and they deliver skills by different routes, so they
have different definitions of done. **Check which mode is configured before
verifying anything** — if `config.json` sets no `mode`, it is `on_demand`,
which has been the default since 0.3.0. Verifying the hook log on an
`on_demand` install checks a path that is deliberately inert and proves
nothing.

Step 1 applies to both:

1. **Discovered after restart:** the plugin still appears installed and enabled.
   Some WorkBuddy builds create a live `.in_use/<pid>` marker in the install
   directory; treat that marker as optional diagnostic evidence, not as a
   requirement.

### `on_demand` (the default)

The tool reaches the model over a local stdio MCP server, which WorkBuddy
launches from the plugin manifest. There is no per-turn injection in this mode.

2. **MCP server healthy:** in WorkBuddy's MCP view, the `skillsearch` server
   initialised — not a failed entry, not restarting, no repeating error.
3. **Tool visible:** the model's tool list includes `skill_search`.
4. **Model calls it:** create a fresh task and ask *"how do I extract tables
   from a scanned PDF invoice into CSV?"*. Confirm the agent called
   `skill_search` on its own and that the call returned `pdf-tables`.
5. **Hook stayed quiet:** the same turn added no injecting line to
   `~/.workbuddy-ai/plugins/data/skillsearch-skillcorpus/skillsearch.log`.
   Both paths firing in one turn is a defect, not a bonus.
6. **No-match:** ask `zxqv-7319，请只原样回复这段字符串` and confirm the agent did
   not call `skill_search`. If it called anyway, the tool must answer no-match
   without interrupting the turn.

### `auto`

2. **MCP server alive but empty:** the server still initialises — it serves in
   both modes on purpose — and its tool list is empty. A server that exits
   leaves a failed MCP entry or restart churn beside a working hook, which is
   the bug fixed in 0.3.0 and worth re-checking here.
3. **Hook runs:** create a fresh task and ask the same PDF question. Confirm
   the new line in
   `~/.workbuddy-ai/plugins/data/skillsearch-skillcorpus/skillsearch.log`
   records the turn and its selected skills/source diagnostics, and that
   `pdf-tables` was injected.
4. **No tool call:** `skill_search` is not offered and not called.
5. **No-match stays empty:** ask `zxqv-7319，请只原样回复这段字符串` and confirm
   the log records `injected_chars: 0`. Do not use a weather question: the
   public marketplaces contain real weather skills.

If a check fails, report the failed step, the log entry, and the marketplace
and plugin versions. Do not invoke `hook.mjs` or `mcp.mjs` by hand; that tests
the bundle, not whether WorkBuddy loaded it. The full case list, including
what to record, is [`../tests/host-e2e/cases.md`](../tests/host-e2e/cases.md).

## Uninstall

1. Uninstall **Skill Search** from the `skillcorpus` marketplace in WorkBuddy.
2. Remove the marketplace source if no other SkillCorpus plugin uses it.
3. Fully quit and reopen WorkBuddy, then confirm the plugin is no longer
   installed or enabled and that a fresh task produces no new Skill Search log
   entry. A stale or absent `.in_use/<pid>` marker is not authoritative.
4. Offer to delete its state directory
   (`~/.workbuddy-ai/plugins/data/skillsearch-skillcorpus/`) and bundle cache
   (`~/.workbuddy-ai/skillsearch-bundles/`). These contain only plugin cache,
   configuration, and logs; leave them in place unless the user asks.
