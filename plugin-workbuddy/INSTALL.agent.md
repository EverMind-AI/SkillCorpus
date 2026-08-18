# INSTALL.agent.md — WorkBuddy installation playbook

You are an agent installing **skillsearch** into WorkBuddy — the host you are
running in. A human asked for this and will read your report. Follow this
file top to bottom.

This section merges into the repository-root `INSTALL.agent.md` alongside the
other hosts; until then it stands alone.

## Rules you must follow throughout

1. **Show before you change.** Before writing or editing any config file,
   show the user the exact diff (or the full new file if it did not exist)
   and wait for their go-ahead unless they already told you to proceed.
2. **Back up first.** Before editing an existing config file, copy it to
   `<name>.bak-skillsearch` next to itself.
3. **Merge, never replace.** Add keys into existing JSON; do not rewrite
   whole files. If an existing config file fails to parse, **stop and tell
   the user** — do not overwrite it.
4. **Stop on ambiguity.** If host detection is unclear, ask instead of
   guessing.
5. **Finish with the verification section.** An install without a passing
   verification is not done — report exactly which step failed.

## Step 0 — detect the host

| Host | It is this host if… |
| --- | --- |
| WorkBuddy | `~/.workbuddy-ai/settings.json` exists, or `WorkBuddy AI.app` is installed, or your own hook payloads carry `"client": "WorkBuddy"` |

Note where this repository is checked out (clone it if the user gave you only
the URL): every path below is relative to the repository root.

## WorkBuddy

WorkBuddy loads plugins, not processes: the seam is a `UserPromptSubmit`
hook the plugin declares, spawned once per turn. Three host behaviours are
load-bearing, all established against 5.3.13 by experiment:

- a failed hook **fails the whole turn** (`HookBlockedError`), so the shipped
  entry never exits non-zero;
- the injected block never reaches the transcript, so the plugin keeps its
  own log;
- the panel's directory-sourced marketplaces install without writing
  `installed_plugins.json`, and such plugins **stop loading after a
  restart** — which is why the steps below write the install records
  directly instead of using the panel.

Build first (the marketplace copy ships `dist/`; a source checkout must
build it):

```bash
npm install --prefix plugin-workbuddy
npm run --prefix plugin-workbuddy build     # produces dist/hook.mjs
```

Then, with the user's go-ahead, perform the file-level install. `<market>`
is the marketplace name and `<version>` is `version` from
`plugin-workbuddy/.codebuddy-plugin/plugin.json` — read both, never invent
them.

1. **Back up** `~/.workbuddy-ai/settings.json`,
   `~/.workbuddy-ai/plugins/installed_plugins.json` and
   `~/.workbuddy-ai/plugins/known_marketplaces.json`.
2. **Register the marketplace** in `known_marketplaces.json`: add a key
   `<market>` shaped like the existing `workbuddy-builtin` entry, with
   `type` `"git"`, `source` naming the repository URL, and
   `installLocation` naming the checkout. Update an existing key rather
   than duplicating it.
3. **Copy into the cache**: the `plugin-workbuddy/` directory (at minimum
   `.codebuddy-plugin/`, `hooks/`, `dist/`) to
   `~/.workbuddy-ai/plugins/cache/<market>/skillsearch/<version>/`.
4. **Record the install** in `installed_plugins.json` under
   `"skillsearch@<market>"`: an array holding one object shaped like the
   existing entries — `scope` `"user"`, `installPath` (the step-3
   directory), `version`, `installedAt`, `lastUpdated` (ISO 8601, now).
5. **Enable** in `settings.json`: add `"skillsearch@<market>": true` to
   `enabledPlugins`. Touch nothing else in that file — not `sandbox`, not
   a `hooks` key (the plugin declares its own hook), not other plugins'
   entries.
6. Tell the user to quit and reopen WorkBuddy.

## Optional capabilities — ask, don't assume

Configuration lives in
`~/.workbuddy-ai/plugins/data/skillsearch-<market>/config.json` (the
environment, set per-command in `hooks.json`, wins over it).

- **A model for the rewriter and gate**: better selection, two small model
  calls per retrieving turn — spent inside the silence between the user
  pressing enter and the reply starting, which this host does not indicate.
  Ask which route to use; leave empty if they don't care.
- **The remote catalog** (`hubEndpoint`, e.g.
  `https://skillhub.evermind.ai`): community skills, but **sends the
  retrieval query to that service on every retrieving turn and downloads
  third-party skill content to disk**, and unvetted catalog skills may
  reference infrastructure this machine lacks. State this plainly and let
  the user opt in; never enable it silently. If enabled, raise `timeoutMs`
  to 4000 and recommend the gate.

## Verification — definition of done

1. **Plugin discovered**: after the restart, the install directory gains an
   `.in_use/<pid>` marker written by the host, and the plugin appears in
   the panel as installed and enabled.
2. **Create a test skill**:

```bash
mkdir -p ~/.workbuddy-ai/skills/pdf-tables
printf -- '---\nname: pdf-tables\ndescription: Extract tables from PDF documents, scanned or native, into CSV.\n---\nUse camelot for native PDFs.\n' > ~/.workbuddy-ai/skills/pdf-tables/SKILL.md
```

3. **Positive probe**: in a fresh WorkBuddy task ask *"扫描版 PDF 发票里的
   表格怎么提取？"* — then confirm the turn's line in
   `~/.workbuddy-ai/plugins/data/skillsearch-<market>/skillsearch.log`
   names `pdf-tables[local]`.
4. **Negative probe**: ask *"今天天气怎么样？"* — confirm that turn's line
   shows `injected_chars: 0`.
5. **Report**: list every file you created or edited (with the diffs), the
   probe results, and how to undo everything (the section below).

If step 3 fails: confirm the restart actually happened (stale `.in_use`
pids from before the restart mean it did not); confirm the skill landed
under a scanned directory (`skillsDirs` defaults to
`~/.workbuddy-ai/skills` and `~/.workbuddy-ai/plugins/cache`); then read
the log's `error` field for the turn. Report what you find rather than
retrying blindly.

## Uninstall

When the user asks to remove skillsearch:

1. Remove the `"skillsearch@<market>"` keys from `enabledPlugins` in
   `settings.json` and from `installed_plugins.json`, the `<market>` entry
   from `known_marketplaces.json`, and the cache directory
   `~/.workbuddy-ai/plugins/cache/<market>/skillsearch/`.
2. Offer to delete the plugin's data directory
   (`~/.workbuddy-ai/plugins/data/skillsearch-<market>/` — config, log,
   index cache) and the bundle cache
   (`~/.workbuddy-ai/skillsearch-bundles/`).
3. Restore or delete the `.bak-skillsearch` backups per the user's call.
4. Show the diffs, same rule as installing.
