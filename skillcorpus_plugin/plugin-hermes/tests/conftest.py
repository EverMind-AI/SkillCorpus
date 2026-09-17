"""Keep the tests out of the user's home directory.

Building an engine registers this host in the shared registry, which lives
under `~/.evermind-skillsearch`. A test that writes there pollutes the machine
it runs on and — worse — makes its own result depend on whatever the developer
happens to have installed, which is how a green suite hides a real ranking
change. Autouse so a new test cannot forget.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_shared_root(tmp_path_factory: pytest.TempPathFactory,
                          monkeypatch: pytest.MonkeyPatch) -> None:
    root: Path = tmp_path_factory.mktemp("skillsearch-home")
    monkeypatch.setenv("SKILLSEARCH_HOME", str(root))
    # A test that wants extra directories asks for them; inheriting the
    # developer's would be the same leak by another route.
    monkeypatch.delenv("SKILLSEARCH_SKILLS_DIRS", raising=False)
