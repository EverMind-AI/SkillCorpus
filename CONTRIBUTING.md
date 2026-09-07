# Contributing

Open an issue for a bug or a substantial change, then create a focused branch
from current `main`. Submit a pull request with the problem, resulting behavior,
and verification results. Maintainers merge after the required checks succeed.

Use Python 3.12 for repository tooling. The core also supports Python 3.10;
the Python plugins support 3.11–3.13. Node plugins use Node 22.19 or newer.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
make install-ci
make check-repo
make test
python -m pytest -q scripts/tests
```

For plugin changes, run the affected package's tests and update its installation
guide. `npm run ci` checks, tests, and builds each Node host plugin. Both top-level
README host tables must include any new supported integration.

Images and videos belong on GitHub attachments or another external host, linked
from Markdown. Do not commit media directories or files larger than 640 KiB.

The versioned manifests share the root `pyproject.toml` version. Run
`python skillcorpus_plugin/scripts/verify_release_versions.py` after a version
change. Describe behavior changes in `docs/releases/vX.Y.Z.md`.

See [engineering and release operations](docs/engineering.md) for CI dependency
updates, compatibility checks, package validation, and publishing.
