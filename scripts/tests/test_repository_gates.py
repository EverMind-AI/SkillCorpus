import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


def load_script(name):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).parents[1] / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sizes = load_script("check_file_sizes")
assets = load_script("check_repo_assets")


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "commit", "--allow-empty", "-m", "base")
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    monkeypatch.setattr(sizes, "_repo_root", lambda: tmp_path)
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.delenv("GITHUB_BASE_REF", raising=False)
    return tmp_path


def oversized(repo):
    (repo / "payload.bin").write_bytes(b"x" * (sizes.MAX_KB * 1024 + 1))
    git(repo, "add", "payload.bin")
    git(repo, "commit", "-m", "add payload")


def test_main_push_checks_tree_even_when_remote_equals_head(repo, monkeypatch):
    oversized(repo)
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    assert sizes.main([]) == 1


def test_pr_checks_new_files_against_base(repo, monkeypatch):
    git(repo, "switch", "-c", "feature")
    oversized(repo)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_BASE_REF", "main")
    assert sizes.main([]) == 1


def test_missing_base_fails_closed(repo):
    assert sizes.main(["--base", "origin/missing"]) == 1


def test_full_scan_checks_exact_limit_and_untracked_files(repo):
    path = repo / "space in name.bin"
    path.write_bytes(b"x" * sizes.MAX_KB * 1024)
    assert sizes.main(["--all"]) == 0
    path.write_bytes(path.read_bytes() + b"x")
    assert sizes.main(["--all"]) == 1


@pytest.mark.parametrize(
    "path", ["docs/logo.PNG", "video/demo.txt", "demo.MP4", "assets/test.json"]
)
def test_media_policy_catches_case_and_directories(path):
    assert assets.find_violations([path])


def test_media_policy_allows_external_links_in_markdown():
    assert not assets.find_violations(["README.md", "docs/setup.md"])
