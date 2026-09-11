# Engineering and release operations

## Required checks

`CI required` covers repository policy, infrastructure regression tests, and
core tests on Python 3.10 and 3.12. `Plugin CI required` covers all applicable
plugin jobs. Both report a result for every PR; documentation-only changes may
skip plugin jobs, but failed or cancelled required work cannot pass the summary.

`Release required` checks that the complete release artifacts can be built on
each PR. It does not publish them.

The main ruleset requires all three checks from GitHub Actions. The existing named
maintainer can bypass through a PR; this does not grant general direct-push
access. Main rejects force-pushes and deletion. Merged head branches are
automatically deleted remotely.

The asset policy checks tracked paths. The 640 KiB size policy checks changes
against the target branch on PRs, and the whole tracked tree on pushes.
`python scripts/check_file_sizes.py --all` performs the full check locally.

## Reproducible CI dependencies

The constraints below pin CI environments without restricting downstream users
to exact dependency versions. Regenerate them with uv when changing dependencies:

```bash
uv pip compile pyproject.toml --extra dev --python-version 3.10 --universal -o requirements/ci.txt
uv pip compile skillcorpus_plugin/engine-python/pyproject.toml --extra dev --python-version 3.11 --universal -o requirements/plugins.txt
uv pip compile requirements/build.in --python-version 3.12 --universal -o requirements/build.txt
```

The three Node plugins use their package-lock files and `npm ci`. The standalone
TypeScript engine tests use the locked OpenClaw test runner and execute every
`tests/*.test.ts` file. DeepSeek Harness remains a private workspace package;
it is distributed in the complete plugin bundle, not as a standalone npm package.

Hermes PR tests use the commit pinned in `plugin-ci.yml`. Weekly compatibility
runs use upstream `main`; failures identify host drift independently of normal
PRs. Real-host/model E2E evidence remains in
[`skillcorpus_plugin/tests/host-e2e`](../skillcorpus_plugin/tests/host-e2e/README.md)
and should be reviewed for plugin releases. It is not implied by unit-test success.

## Publishing a version

Merging a normal PR runs CI; it does not create a release.

1. Update the root and plugin versions together. Add `docs/releases/vX.Y.Z.md`
   with **What’s Changed**, an **Upgrade Note** when needed, and a final
   **Full Changelog** comparing the previous release tag with this tag.
2. Merge the version PR after CI and the release build pass.
3. Push `vX.Y.Z` pointing to that merged commit on `main`. The tag is the
   publication trigger. The workflow checks source versions, notes, ancestry,
   the full core/plugin test suites, and the complete set of 10 payloads.
4. The workflow creates a draft GitHub Release, downloads its 11 attachments
   (including `SHA256SUMS`), verifies them, and publishes the draft. Checksums
   use flat filenames so a normal download directory can run
   `sha256sum -c SHA256SUMS` (or `shasum -a 256 -c SHA256SUMS` on macOS).

Existing releases are never overwritten by a build rerun. If upload or download
verification fails, the draft stays available for inspection. Resolve that draft
before retrying publication; do not move an existing version tag.

For a build/test dry run, use Actions → Release → Run workflow → `validate`.
This does not publish anything. To correct an existing release's text, first
merge its note file change, then run Release on `main` with `operation=sync-notes`
and the existing `tag`. This updates only the release body.

## Optional PyPI publication

GitHub attachments are the default distribution channel. The root PyPI job is
enabled only when repository variable `PYPI_PUBLISH_ENABLED` equals `true`.
Before enabling it, an owner of the PyPI `skillcorpus` project must configure a
Trusted Publisher (or pending publisher) with these exact values:

| Field | Value |
| --- | --- |
| GitHub owner | `EverMind-AI` |
| Repository | `SkillCorpus` |
| Workflow | `release.yml` |
| Environment | `release` |

This step requires access to the PyPI account; GitHub administrator permissions
alone cannot create it. An `invalid-publisher` error means the OIDC publisher
does not match. Never enable the variable before that configuration is verified.
The workflow publishes only the root Python distributions. Plugin PyPI and npm
publication need their own publisher setup and are not currently enabled.

## Repository settings

Keep Actions' default token permission read-only and PR-approval permission off.
Publishing jobs request their additional permissions explicitly. Enable secret
scanning, push protection, dependency alerts/security updates, and CodeQL in
GitHub settings. These settings are separate from version-controlled workflows.

GitHub uses the root `.github/workflows/` files. Legacy `.gitlab-ci.yml` and
`skillcorpus_plugin/.github/workflows/ci.yml` are retained for downstream mirrors;
they do not provide checks for this GitHub repository.
