#!/usr/bin/env python3
import faulthandler, sys, os
faulthandler.enable(file=sys.stderr, all_threads=True)

"""Standalone SkillsBench runner using the raven agent framework.

Runs a single SkillsBench task: builds the task's Docker image, starts a
container, drives the raven agent against it, then runs the task verifier
and writes result.json.

Usage:
    python run_raven.py tasks/dialogue-parser --model <model-name>
"""

import argparse
import asyncio
import http.client
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from skill_attachments import inject_skill_attachments, skill_resources_note, skill_scripts_manifest
import sqlite3

# Skill loading helpers
def load_gold_skills(task_dir: Path) -> tuple[str, list[str], list[Path]]:
    """Load gold skills from the task's environment/skills/ directory.

    Returns (skill_content, skill_names, skill_dirs) where skill_dirs
    are the on-disk directories for attachment injection.
    """
    skills_dir = task_dir / "environment" / "skills"
    if not skills_dir.exists():
        return "", [], []

    skill_names = []
    skill_parts = []
    skill_dirs = []
    for skill_path in sorted(skills_dir.iterdir()):
        if not skill_path.is_dir():
            continue
        skill_md = skill_path / "SKILL.md"
        if not skill_md.exists():
            continue
        name = skill_path.name
        body = skill_md.read_text(encoding="utf-8")
        skill_names.append(name)
        skill_dirs.append(skill_path)
        skill_parts.append(f"### Skill: {name}\n\n{body}")

    skill_content = "\n\n---\n\n".join(skill_parts) if skill_parts else ""
    log.info(f"Loaded {len(skill_names)} gold skills for {task_dir.name}: {skill_names}")
    return skill_content, skill_names, skill_dirs


def inject_gold_skill_attachments(cid, skill_names, skill_dirs):
    """Copy gold skill attachment files into the container at /skills/<name>/."""
    import shutil
    import tempfile
    import subprocess

    subprocess.run(["docker", "exec", cid, "mkdir", "-p", "/skills"],
                   timeout=30, check=False, capture_output=True)

    total = 0
    for name, src_dir in zip(skill_names, skill_dirs):
        files = [f for f in src_dir.rglob("*") if f.is_file() and f.name != "SKILL.md"]
        if not files:
            continue
        stage = Path(tempfile.mkdtemp(prefix="_goldskill_"))
        try:
            for f in files:
                rel = f.relative_to(src_dir)
                dst = stage / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
            dest = f"/skills/{name}"
            subprocess.run(["docker", "exec", cid, "mkdir", "-p", dest],
                           timeout=30, check=False, capture_output=True)
            r = subprocess.run(["docker", "cp", f"{stage}/.", f"{cid}:{dest}"],
                               timeout=120, check=False, capture_output=True)
            if r.returncode == 0:
                total += len(files)
                log.info(f"gold skill {name}: injected {len(files)} file(s) -> {dest}")
            else:
                log.warning(f"gold skill {name}: docker cp failed: {r.stderr.decode()[:200]}")
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    return total


# ── Pre-retrieved skill loading ─────────────────────────────────────

def load_preretrieved_skills(
    skill_outputs_dir: Path,
    task_id: str,
    gate_model: str,
    inject_max: int = 5,
    mass_library_db: Path | None = None,
    gate_fn=None,
) -> tuple[str, list[str]]:
    """Load pre-retrieved skills for a task.

    JSON files provide skill_name + description; body is looked up from DB.
    If after_gate dir is empty or missing, returns no skills (no fallback).

    gate_fn: optional callable(candidates) -> kept_subset, where candidates is a
    list of dicts {name, description, body}. Used to drop irrelevant skills
    (e.g. an LLM relevance gate) before building the injected content.
    """
    task_dir = skill_outputs_dir / task_id
    if not task_dir.exists():
        log.warning(f"No skill outputs for task {task_id}")
        return "", [], []

    gate_dir = task_dir / f"after_gate_{gate_model}"
    if not gate_dir.exists():
        log.info(f"No after_gate dir for model {gate_model}, task {task_id} — no skills")
        return "", [], []

    skill_files = sorted(gate_dir.glob("*.json"))[:inject_max]
    if not skill_files:
        log.info(f"No skills in {gate_dir}")
        return "", [], []

    db_conn = None
    if mass_library_db and mass_library_db.exists():
        db_conn = sqlite3.connect(str(mass_library_db))

    candidates = []
    try:
        for sf in skill_files:
            try:
                data = json.loads(sf.read_text(encoding="utf-8"))
            except Exception:
                continue
            name = data.get("skill_name", sf.stem)
            desc = data.get("description", "")

            # Authoritative body = retrieval JSON (what was actually retrieved);
            # DB lookup only as fallback (mirrors skillsbench-eval paper runner).
            body = data.get("body", "") or ""
            if not body and db_conn:
                cur = db_conn.cursor()
                cur.execute("SELECT body FROM skills WHERE name = ? LIMIT 1", (name,))
                row = cur.fetchone()
                if row:
                    body = row[0]

            if not body:
                log.warning(f"No body (JSON or DB) for skill {name}, skipping")
                continue

            candidates.append({"name": name, "description": desc, "body": body})
    finally:
        if db_conn:
            db_conn.close()

    # Optional relevance gate: drop candidates the gate judges irrelevant.
    if gate_fn is not None and candidates:
        try:
            candidates = gate_fn(candidates)
        except Exception as e:
            log.warning(f"gate_fn failed ({e}); keeping all candidates")

    skill_names = [c["name"] for c in candidates]
    skill_bodies = [c["body"] for c in candidates]
    skill_parts = [
        f"### Skill: {c['name']}\n"
        + (f"_{c['description']}_\n\n" if c.get("description") else "\n")
        + c["body"]
        for c in candidates
    ]
    skill_content = "\n\n---\n\n".join(skill_parts) if skill_parts else ""
    log.info(f"Loaded {len(skill_names)} skills for {task_id}: {skill_names}")
    return skill_content, skill_names, skill_bodies


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "raven"))

import importlib

def _direct_import(module_path):
    return importlib.import_module(module_path)

_tools_base = _direct_import("raven.agent.tools.base")
_tools_registry = _direct_import("raven.agent.tools.registry")
Tool = _tools_base.Tool
ToolRegistry = _tools_registry.ToolRegistry


def _import_litellm_provider():
    from raven.providers.litellm_provider import LiteLLMProvider
    return LiteLLMProvider


def _import_skill_service():
    from raven.memory_engine.skill_forge.catalog import LocalSkillCatalog
    return LocalSkillCatalog


def _import_agent_loop():
    from raven.agent.loop import AgentLoop
    return AgentLoop


def _import_session_manager():
    from raven.session.manager import SessionManager
    return SessionManager


def _patch_skillhub_retry(timeout_s: float = 10.0, max_retries: int = 3):
    """Increase SkillHub timeout and add retry logic to HubSkillSource.search."""
    import raven.skill_hub.client as _shc
    _shc._DEFAULT_TIMEOUT_S = timeout_s

    from raven.memory_engine.skill_forge.hub_source import HubSkillSource
    _orig_search = HubSkillSource.search

    async def _search_with_retry(self, query, history, k):
        import asyncio as _aio
        last_exc = None
        for attempt in range(max_retries):
            try:
                return await _orig_search(self, query, history, k)
            except Exception as exc:
                last_exc = exc
                if attempt < max_retries - 1:
                    wait = 1.0 * (2 ** attempt)
                    log.warning("SkillHub search attempt %d/%d failed (%s), retrying in %.1fs",
                                attempt + 1, max_retries, exc, wait)
                    await _aio.sleep(wait)
        raise last_exc

    HubSkillSource.search = _search_with_retry

    from raven.memory_engine.skill_forge.gate import LLMGateFilter
    from raven.agent.loop.recovery import has_thinking

    _orig_gate_filter = LLMGateFilter.filter

    async def _filter_with_recovery(self, task, candidates, available_tools=None):
        import asyncio as _aio
        if not candidates:
            return []
        catalog, by_id = self._build_catalog(candidates)
        prompt = self._build_prompt(task, catalog, available_tools)
        messages = [{"role": "user", "content": prompt}]

        prefill_retries = 0
        empty_retries = 0
        error_retries = 0
        max_prefill = 2
        max_empty = 3
        max_error = 3

        while True:
            try:
                resp = await _aio.wait_for(
                    self._provider.chat_with_retry(
                        messages=messages,
                        model=self._model or None,
                        max_tokens=self._max_tokens,
                        temperature=self._temperature,
                    ),
                    timeout=180.0,
                )
                content = resp.content or ""
                if getattr(resp, "finish_reason", None) == "error":
                    raise RuntimeError(content or "provider error")
            except Exception as exc:
                if error_retries < max_error:
                    error_retries += 1
                    log.warning("LLM gate call failed (%s); retry %d/%d",
                                exc, error_retries, max_error)
                    await _aio.sleep(2.0 * error_retries)
                    continue
                log.warning("LLM gate call failed (%s); falling back to top-N", exc)
                return candidates[: self._legacy_top_k]

            import re as _re
            clean = _re.sub(r"<think>[\s\S]*?</think>", "", content).strip()

            if clean:
                try:
                    plan, selected_ids = self._parse_response(content)
                except ValueError as exc:
                    log.warning("LLM gate response unparseable (%s); falling back to top-N", exc)
                    return candidates[: self._legacy_top_k]
                out = []
                for sid in selected_ids:
                    if len(out) >= self._max_select:
                        break
                    hit = by_id.get(sid)
                    if hit is not None:
                        out.append(hit)
                log.info("LLM gate: candidates=%d -> selected=%d %s",
                         len(candidates), len(out), [h.name for h in out])
                return out

            if has_thinking(resp) and prefill_retries < max_prefill:
                prefill_retries += 1
                log.warning("LLM gate: thinking-only prefill %d/%d", prefill_retries, max_prefill)
                messages.append({"role": "assistant", "content": content,
                                 "reasoning_content": resp.reasoning_content,
                                 "thinking_blocks": getattr(resp, "thinking_blocks", None)})
                continue

            if empty_retries < max_empty:
                empty_retries += 1
                log.warning("LLM gate: empty retry %d/%d", empty_retries, max_empty)
                continue

            log.warning("LLM gate: recovery exhausted; falling back to top-N")
            return candidates[: self._legacy_top_k]

    LLMGateFilter.filter = _filter_with_recovery


def build_assistant_message(content, tool_calls=None):
    msg = {"role": "assistant", "content": content or ""}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("run_raven")


class TokenTracker:
    """Wraps a provider to accumulate token usage across all LLM calls."""

    def __init__(self, provider):
        self._provider = provider
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.n_calls = 0

    def __getattr__(self, name):
        return getattr(self._provider, name)

    async def chat_with_retry(self, **kwargs):
        response = await self._provider.chat_with_retry(**kwargs)
        self.n_calls += 1
        usage = getattr(response, "usage", None) or {}
        if isinstance(usage, dict):
            self.prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
            self.completion_tokens += int(usage.get("completion_tokens", 0) or 0)
            self.total_tokens += int(usage.get("total_tokens", 0) or 0)
        return response

    def get_usage(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "n_llm_calls": self.n_calls,
        }


_agent_loops: dict[str, object] = {}


def get_agent_loop(
    config_path: Path,
    provider,
    model: str,
    max_iterations: int,
    workspace: Path,
):
    """Return a cached raven AgentLoop using a proper config file."""
    AgentLoop = _import_agent_loop()
    SessionManager = _import_session_manager()

    key = str(config_path.resolve())
    if key not in _agent_loops:
        from raven.config.loader import load_config, set_config_path
        set_config_path(config_path)
        config = load_config(config_path)
        sf_config = config.skill_forge

        loop = AgentLoop(
            provider=provider,
            workspace=workspace,
            model=model,
            max_iterations=max_iterations,
            skill_forge_config=sf_config,
            session_manager=SessionManager(workspace),
            interactive=False,
        )
        _agent_loops[key] = loop
    else:
        loop = _agent_loops[key]
        loop.workspace = workspace
        loop.context.workspace = workspace
        from raven.memory_engine.consolidate.consolidator import MemoryStore
        loop.context.memory = MemoryStore(workspace)
        from raven.session.manager import SessionManager
        loop.sessions = SessionManager(workspace)
    return _agent_loops[key]


# ── Docker API utilities ────────────────────────────────────────────

DOCKER_SOCK = "/var/run/docker.sock"


def _unix_sock():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(DOCKER_SOCK)
    return s


def _api_get(path):
    conn = http.client.HTTPConnection("localhost")
    conn.sock = _unix_sock()
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return json.loads(resp.read().decode())
    finally:
        conn.close()


def _api_post(path, body=None):
    conn = http.client.HTTPConnection("localhost")
    conn.sock = _unix_sock()
    try:
        headers = {"Content-Type": "application/json"} if body else {}
        conn.request("POST", path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode()
        return json.loads(raw) if raw.strip() else {}
    finally:
        conn.close()


def docker_exec(cid, command, cwd=None, user=None, timeout=300):
    """Execute command in container via Docker API. Returns (stdout, stderr, rc)."""
    sess = f"rv{int(time.time() * 1e9)}{os.getpid()}"
    edir = f"/tmp/_rv/{sess}"

    script = "#!/bin/bash\n"
    script += f"mkdir -p '{edir}'\n"
    if cwd:
        script += f"cd '{cwd}' 2>/dev/null\n"
    script += f"( {command} ) > '{edir}/out' 2> '{edir}/err'\n"
    script += f"echo $? > '{edir}/rc'\n"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write(script)
        tmp = f.name
    os.chmod(tmp, 0o755)

    script_path = f"/tmp/_rv_run_{sess}.sh"
    try:
        subprocess.run(["docker", "cp", tmp, f"{cid}:{script_path}"],
                       timeout=30, check=False, capture_output=True)
    finally:
        os.unlink(tmp)

    cfg = {"AttachStdout": False, "AttachStderr": False, "Cmd": ["bash", script_path]}
    if user:
        cfg["User"] = str(user)

    resp = _api_post(f"/containers/{cid}/exec", json.dumps(cfg).encode())
    exec_id = resp.get("Id", "")
    if not exec_id:
        return "", "exec create failed", 1

    _api_post(f"/exec/{exec_id}/start", json.dumps({"Detach": True}).encode())

    tmpdir = tempfile.mkdtemp(prefix="_rv_res_")
    deadline = time.time() + timeout
    stdout_text, stderr_text, rc = "", "", 1

    try:
        while time.time() < deadline:
            r = subprocess.run(["docker", "cp", f"{cid}:{edir}/rc", f"{tmpdir}/rc"],
                               timeout=10, check=False, capture_output=True)
            if r.returncode == 0:
                subprocess.run(["docker", "cp", f"{cid}:{edir}/out", f"{tmpdir}/out"],
                               timeout=30, check=False, capture_output=True)
                subprocess.run(["docker", "cp", f"{cid}:{edir}/err", f"{tmpdir}/err"],
                               timeout=30, check=False, capture_output=True)
                rc_p = Path(tmpdir) / "rc"
                out_p = Path(tmpdir) / "out"
                err_p = Path(tmpdir) / "err"
                if rc_p.exists():
                    rc = int(rc_p.read_text().strip() or "1")
                if out_p.exists():
                    stdout_text = out_p.read_text()
                if err_p.exists():
                    stderr_text = err_p.read_text()
                break
            time.sleep(0.5)
        else:
            stderr_text = f"timed out after {timeout}s"
            rc = 124
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        try:
            cleanup = {"AttachStdout": False, "AttachStderr": False,
                       "Cmd": ["rm", "-rf", edir, script_path]}
            cr = _api_post(f"/containers/{cid}/exec", json.dumps(cleanup).encode())
            if cr.get("Id"):
                _api_post(f"/exec/{cr['Id']}/start", json.dumps({"Detach": True}).encode())
        except Exception:
            pass

    return stdout_text, stderr_text, rc


def docker_cp(src, dst):
    subprocess.run(["docker", "cp", src, dst], timeout=120, check=False, capture_output=True)


UV_BIN = str(Path(__file__).resolve().parent / "uv_bin" / "uv")
UVX_BIN = str(Path(__file__).resolve().parent / "uv_bin" / "uvx")

# Optional network configuration for restricted environments. All default to
# off (direct network, official package indexes).
#   BUILD_PROXY  - http(s) proxy URL passed to docker build and set inside containers
#   PIP_MIRROR   - PyPI index URL configured inside containers (pip + uv)
#   APT_MIRROR   - mirror host substituted for archive/security.ubuntu.com
#   MAVEN_MIRROR - Maven mirror URL written to ~/.m2/settings.xml
BUILD_PROXY = os.environ.get("BUILD_PROXY", "")
PIP_MIRROR = os.environ.get("PIP_MIRROR", "")
APT_MIRROR = os.environ.get("APT_MIRROR", "")
MAVEN_MIRROR = os.environ.get("MAVEN_MIRROR", "")


def _inject_offline_deps(cid, task_dir):
    """Inject curl wrapper + uv binary + mirror configs into container so test.sh needs minimal network."""
    tsh = task_dir / "tests" / "test.sh"
    if not tsh.exists():
        return
    all_content = []
    for sh in (task_dir / "tests").glob("*.sh"):
        all_content.append(sh.read_text())
    content = "\n".join(all_content)
    has_curl_uv = "astral.sh/uv" in content
    has_uv = "uv add" in content or "uv init" in content or "uv run" in content or "uv pip" in content
    has_uvx = "uvx " in content or "uvx\n" in content
    has_pip = "pip3 install" in content or "pip install" in content
    has_apt = "apt-get" in content

    if has_apt and APT_MIRROR:
        docker_exec(cid,
            f'sed -i "s|http://archive.ubuntu.com|http://{APT_MIRROR}|g; '
            f's|http://security.ubuntu.com|http://{APT_MIRROR}|g" '
            f'/etc/apt/sources.list.d/ubuntu.sources 2>/dev/null; '
            f'sed -i "s|http://archive.ubuntu.com|http://{APT_MIRROR}|g; '
            f's|http://security.ubuntu.com|http://{APT_MIRROR}|g" '
            f'/etc/apt/sources.list 2>/dev/null; true',
            user="root")
        log.info(f"Configured apt mirror → {APT_MIRROR}")

    if has_curl_uv and os.path.exists(UV_BIN):
        docker_exec(cid, "mkdir -p /root/.local/bin", user="root")
        subprocess.run(["docker", "cp", UV_BIN, f"{cid}:/root/.local/bin/uv"],
                       capture_output=True, timeout=30)
        subprocess.run(["docker", "cp", UVX_BIN, f"{cid}:/root/.local/bin/uvx"],
                       capture_output=True, timeout=30)
        docker_exec(cid, "chmod +x /root/.local/bin/uv /root/.local/bin/uvx", user="root")
        env_script = (
            'printf "%s\\n"'
            ' "#!/bin/sh"'
            ' "case \\":\\${PATH}:\\" in"'
            ' "    *:/root/.local/bin:*) ;;"'
            ' "    *) export PATH=\\"/root/.local/bin:\\$PATH\\" ;;"'
            ' "esac"'
            ' > /root/.local/bin/env'
        )
        docker_exec(cid, env_script, user="root")
        wrapper_script = (
            'if [ -f /usr/bin/curl ] && [ ! -f /usr/bin/curl.real ]; then '
            'mv /usr/bin/curl /usr/bin/curl.real; '
            'printf "%s\\n"'
            ' "#!/bin/bash"'
            ' "for arg in \\"\\$@\\"; do"'
            ' "    if [[ \\"\\$arg\\" == *\\"astral.sh/uv\\"* ]]; then"'
            ' "        echo \\"#!/bin/sh\\""'
            ' "        echo \\"echo uv already installed\\""'
            ' "        exit 0"'
            ' "    fi"'
            ' "done"'
            ' "exec /usr/bin/curl.real \\"\\$@\\""'
            ' > /usr/bin/curl; '
            'chmod +x /usr/bin/curl; fi'
        )
        docker_exec(cid, wrapper_script, user="root")
        log.info("Injected uv binary + curl wrapper")

    if (has_curl_uv or has_uv or has_uvx) and PIP_MIRROR:
        docker_exec(cid,
            f'mkdir -p /root/.config/uv && printf "%s\\n"'
            f' "index-url = \\"{PIP_MIRROR}\\""'
            f' > /root/.config/uv/uv.toml',
            user="root")
        log.info("Configured uv index mirror")

    if has_pip and PIP_MIRROR:
        from urllib.parse import urlparse
        mirror_host = urlparse(PIP_MIRROR).hostname or ""
        docker_exec(cid,
            f'mkdir -p /root/.pip && cat > /root/.pip/pip.conf << EOF\n'
            f'[global]\n'
            f'index-url = {PIP_MIRROR}\n'
            f'trusted-host = {mirror_host}\n'
            f'EOF',
            user="root")

    has_mvn = "mvn " in content or "maven" in content.lower()
    if has_mvn and MAVEN_MIRROR:
        maven_settings = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<settings>\n'
            '  <mirrors>\n'
            '    <mirror>\n'
            '      <id>mirror</id>\n'
            '      <mirrorOf>*</mirrorOf>\n'
            f'      <url>{MAVEN_MIRROR}</url>\n'
            '    </mirror>\n'
            '  </mirrors>\n'
            '</settings>'
        )
        for home in ["/root", "/home/travis"]:
            docker_exec(cid,
                f'mkdir -p {home}/.m2 && cat > {home}/.m2/settings.xml << \'MVNEOF\'\n'
                f'{maven_settings}\n'
                f'MVNEOF\n'
                f'test -f {home}/.m2/passed_settings.xml && cp {home}/.m2/settings.xml {home}/.m2/passed_settings.xml',
                user="root")
        log.info(f"Configured Maven mirror → {MAVEN_MIRROR}")


# ── Container lifecycle ─────────────────────────────────────────────

def build_image(task_dir, image_name):
    import fcntl
    r = subprocess.run(
        ["docker", "images", "-q", image_name],
        capture_output=True, text=True, timeout=10,
    )
    if r.stdout.strip():
        log.info(f"Image {image_name} already exists, skipping build")
        return
    prebuilt = Path(__file__).parent / "prebuilt_images" / f"{task_dir.name}.tar"
    if prebuilt.exists():
        lock_path = Path(f"/tmp/_imglock_{task_dir.name}.lock")
        with open(lock_path, "w") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            r = subprocess.run(
                ["docker", "images", "-q", image_name],
                capture_output=True, text=True, timeout=10,
            )
            if r.stdout.strip():
                log.info(f"Image {image_name} loaded by another process, reusing")
                return
            local_copy = Path(f"/tmp/_load_{task_dir.name}_{os.getpid()}.tar")
            try:
                log.info(f"Copying {prebuilt} to local disk")
                shutil.copy2(str(prebuilt), str(local_copy))
                log.info(f"Loading prebuilt image from local copy")
                r = subprocess.run(
                    ["docker", "load", "-i", str(local_copy)],
                    timeout=600, capture_output=True,
                )
                if r.returncode == 0:
                    return
                log.warning(f"docker load failed, falling back to build: {r.stderr.decode()[:200]}")
            finally:
                local_copy.unlink(missing_ok=True)
    env_dir = task_dir / "environment"
    log.info(f"Building image {image_name} from {env_dir}")
    cmd = ["docker", "build"]
    if BUILD_PROXY:
        cmd += ["--build-arg", f"http_proxy={BUILD_PROXY}",
                "--build-arg", f"https_proxy={BUILD_PROXY}"]
    cmd += ["-t", image_name, "."]
    r = subprocess.run(cmd, cwd=str(env_dir), timeout=600, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"docker build failed: {r.stderr.decode()[:500]}")


def start_container(image_name, container_name, cpus=1, memory="4G", mounts=None):
    subprocess.run(["docker", "rm", "-f", container_name],
                   timeout=30, check=False, capture_output=True)
    cmd = ["docker", "run", "-d", "--name", container_name,
           f"--cpus={cpus}", f"--memory={memory}"]
    if BUILD_PROXY:
        cmd += ["-e", f"http_proxy={BUILD_PROXY}",
                "-e", f"https_proxy={BUILD_PROXY}",
                "-e", f"HTTP_PROXY={BUILD_PROXY}",
                "-e", f"HTTPS_PROXY={BUILD_PROXY}",
                "-e", "no_proxy=127.0.0.1,localhost",
                "-e", "NO_PROXY=127.0.0.1,localhost"]
    if PIP_MIRROR:
        cmd += ["-e", f"UV_INDEX_URL={PIP_MIRROR}",
                "-e", f"UV_DEFAULT_INDEX={PIP_MIRROR}",
                "-e", f"UV_INDEX={PIP_MIRROR}"]
    for src, dst in (mounts or []):
        cmd.extend(["-v", f"{src}:{dst}:ro"])
    cmd.extend([image_name, "sh", "-c", "sleep infinity"])
    r = subprocess.run(cmd, timeout=300, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"docker run failed: {r.stderr.decode()[:500]}")
    cid = subprocess.run(
        ["docker", "inspect", "--format", "{{.Id}}", container_name],
        timeout=10, capture_output=True, text=True,
    ).stdout.strip()
    if BUILD_PROXY:
        subprocess.run(
            ["docker", "exec", container_name, "sh", "-c",
             'cat > /etc/apt/apt.conf.d/proxy.conf << PROXYEOF\n'
             f'Acquire::http::Proxy "{BUILD_PROXY}";\n'
             f'Acquire::https::Proxy "{BUILD_PROXY}";\n'
             'PROXYEOF'],
            timeout=10, check=False, capture_output=True,
        )
    return cid


def stop_container(container_name):
    try:
        subprocess.run(["docker", "rm", "-f", container_name],
                       timeout=60, check=False, capture_output=True)
    except subprocess.TimeoutExpired:
        pass


# ── DockerExecTool ──────────────────────────────────────────────────

class DockerExecTool(Tool):
    """Shell execution routed to a Docker container via Engine API."""

    _MAX_OUTPUT = 10_000

    def __init__(self, container_id: str, default_timeout: int = 120, working_dir: str = "/app"):
        self._cid = container_id
        self._default_timeout = default_timeout
        self._working_dir = working_dir

    @property
    def name(self) -> str:
        return "exec"

    @property
    def description(self) -> str:
        return "Execute a shell command in the task environment and return its output."

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "working_dir": {"type": "string", "description": "Working directory (default: /app)"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 120, max 600)",
                            "minimum": 1, "maximum": 600},
            },
            "required": ["command"],
        }

    async def execute(self, command: str, working_dir: str | None = None,
                      timeout: int | None = None, **kw) -> str:
        cwd = working_dir or self._working_dir
        t = min(timeout or self._default_timeout, 600)

        stdout, stderr, rc = await asyncio.to_thread(
            docker_exec, self._cid, command, cwd=cwd, timeout=t,
        )

        parts = []
        if stdout:
            parts.append(stdout)
        if stderr and stderr.strip():
            parts.append(f"STDERR:\n{stderr}")
        parts.append(f"\nExit code: {rc}")
        result = "\n".join(parts) if parts else "(no output)"

        if len(result) > self._MAX_OUTPUT:
            half = self._MAX_OUTPUT // 2
            result = (result[:half]
                      + f"\n\n... ({len(result) - self._MAX_OUTPUT:,} chars truncated) ...\n\n"
                      + result[-half:])
        return result


class DockerReadFileTool(Tool):
    _MAX_CHARS = 128_000
    _DEFAULT_LIMIT = 2000

    def __init__(self, container_id: str):
        self._cid = container_id

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read the contents of a file. Returns numbered lines. "
            "Use offset and limit to paginate through large files."
        )

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to read"},
                "offset": {"type": "integer", "description": "Line number to start reading from (1-indexed, default 1)", "minimum": 1},
                "limit": {"type": "integer", "description": "Maximum number of lines to read (default 2000)", "minimum": 1},
            },
            "required": ["path"],
        }

    async def execute(self, path: str | None = None, file_path: str | None = None,
                      offset: int = 1, limit: int | None = None, **kw) -> str:
        p = path or file_path
        if not p:
            return "Error: missing required parameter 'path'"
        n = limit or self._DEFAULT_LIMIT
        cmd = f"sed -n '{offset},{offset + n - 1}p' {shlex_quote(p)}"
        stdout, stderr, rc = await asyncio.to_thread(
            docker_exec, self._cid, cmd, timeout=30,
        )
        if rc != 0:
            return f"Error reading {p}: {stderr.strip() or 'exit code ' + str(rc)}"
        lines = stdout.splitlines()
        numbered = "\n".join(f"{offset + i:6d}\t{ln}" for i, ln in enumerate(lines))
        if len(numbered) > self._MAX_CHARS:
            numbered = numbered[: self._MAX_CHARS] + f"\n... ({len(numbered) - self._MAX_CHARS} chars truncated)"
        return numbered or "(empty file)"


def shlex_quote(s: str) -> str:
    import shlex
    return shlex.quote(s)


class TaskCompleteTool(Tool):
    def __init__(self):
        self.completed = False
        self.summary = ""

    @property
    def name(self) -> str:
        return "task_complete"

    @property
    def description(self) -> str:
        return "Call this when you have finished the task. Provide a brief summary of what you did."

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Brief summary of what was accomplished"},
            },
            "required": ["summary"],
        }

    async def execute(self, summary: str = "", **kw) -> str:
        self.completed = True
        self.summary = summary
        return "Task marked as complete."


# ── Agent loop (manual fallback, not normally used with raven) ──────

async def run_agent(
    provider,
    tools: ToolRegistry,
    messages: list[dict],
    model: str,
    max_iterations: int = 40,
    complete_tool: TaskCompleteTool | None = None,
) -> tuple[list[dict], int]:
    """Run the raven-style agent loop. Returns (messages, n_tool_calls)."""
    n_tool_calls = 0
    tool_defs = tools.get_definitions()

    for iteration in range(max_iterations):
        log.info(f"Iteration {iteration + 1}/{max_iterations}")

        response = await provider.chat_with_retry(
            messages=messages, tools=tool_defs, model=model,
        )

        if response.finish_reason == "error":
            log.error(f"LLM error: {(response.content or '')[:200]}")
            break

        if response.has_tool_calls:
            tc_dicts = [tc.to_openai_tool_call() for tc in response.tool_calls]
            messages.append(build_assistant_message(response.content, tool_calls=tc_dicts))

            for tc in response.tool_calls:
                n_tool_calls += 1
                log.info(f"  Tool: {tc.name}({json.dumps(tc.arguments, ensure_ascii=False)[:120]})")
                result = await tools.execute(tc.name, tc.arguments)
                messages.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "name": tc.name, "content": result,
                })
                if complete_tool and complete_tool.completed:
                    log.info(f"  Agent signaled completion: {complete_tool.summary[:100]}")
                    return messages, n_tool_calls
        else:
            messages.append(build_assistant_message(response.content))
            log.info("Agent finished (no more tool calls)")
            break

    return messages, n_tool_calls


# ── System prompt ───────────────────────────────────────────────────

def build_system_prompt(skill_content: str, skills_directory: str) -> str:
    parts = [
        """You are a skilled software engineer solving a programming task inside a Docker container.

## Environment
- You have a single tool: `exec` to run shell commands in the container.
- Working directory is /app. The task files are already in the container.
- When you are done, call `task_complete` with a brief summary.

## Guidelines
- Read files before modifying them.
- Check command output and handle errors.
- Do not assume — verify by running commands.
- Be efficient: plan before acting, minimize unnecessary commands."""
    ]

    if skill_content:
        parts.append(f"## Active Skills\n\nThe following skills provide domain knowledge for this task:\n\n{skill_content}")

    if skills_directory:
        parts.append(f"## Available Skills\n\n{skills_directory}")

    return "\n\n---\n\n".join(parts)


# ── Task parsing ────────────────────────────────────────────────────

def parse_task_config(task_dir: Path) -> dict:
    toml_path = task_dir / "task.toml"
    if not toml_path.exists():
        return {}
    with open(toml_path, "rb") as f:
        return tomllib.load(f)


def _build_skill_hub_router_config(args):
    """Build a SkillForgeRouterConfig with Skill Hub endpoint when --skill-hub-endpoint is set."""
    hub_endpoint = getattr(args, "skill_hub_endpoint", None)
    if not hub_endpoint:
        return None
    from raven.config.raven import SkillForgeRouterConfig, HubSourceConfig
    hub_cfg = HubSourceConfig(
        endpoint=hub_endpoint,
        api_key=getattr(args, "skill_hub_api_key", None),
        timeout_s=30.0,
    )
    return SkillForgeRouterConfig(hub=hub_cfg)


# ── Spine-based turn runner ─────────────────────────────────────────

async def _run_turn_text(agent_loop, message: str, *, session_key: str, chat_id: str) -> str:
    """Run one USER turn through raven's spine run_turn and return the reply text."""
    from raven.spine import ChatType, Origin, Source, Text, TurnRequest

    parts: list[str] = []

    async def _collect(ev: object) -> None:
        if isinstance(ev, Text):
            parts.append(ev.content)

    await agent_loop.run_turn(
        TurnRequest(
            origin=Origin.USER,
            source=Source(channel="benchmark", chat_id=chat_id, sender_id="user", chat_type=ChatType.DM),
            text=message,
            conversation=session_key,
        ),
        _collect,
        lambda: [],
        stream=False,
    )
    return "".join(parts)


# ── Main runner ─────────────────────────────────────────────────────

async def run_task(task_dir: Path, args):
    task_id = task_dir.name
    config = parse_task_config(task_dir)
    env_config = config.get("environment", {})
    agent_timeout = int(config.get("agent", {}).get("timeout_sec", 900))
    verifier_timeout = int(config.get("verifier", {}).get("timeout_sec", 1800))

    prefix = getattr(args, 'container_prefix', 'raven-bench')
    image_name = f"{prefix}-img-{task_id}"
    container_name = f"{prefix}-{task_id}"

    build_image(task_dir, image_name)
    cpus = env_config.get("cpus", 1)
    memory = f"{env_config.get('memory_mb', 4096)}M"
    mounts = []
    if args.skill_pool:
        pool = Path(args.skill_pool).resolve()
        mounts.append((str(pool), str(pool)))
    cid = start_container(
        image_name, container_name, cpus=cpus, memory=memory, mounts=mounts,
    )
    log.info(f"Container started: {container_name} ({cid[:12]})")

    try:
        docker_exec(cid, "mkdir -p /logs/agent /logs/verifier /logs/artifacts")

        if (task_dir / "instruction.md").exists():
            docker_cp(str(task_dir / "instruction.md"), f"{cid}:/instruction.md")
        if (task_dir / "solution").is_dir():
            docker_cp(str(task_dir / "solution") + "/.", f"{cid}:/solution/")
        if (task_dir / "tests").is_dir():
            docker_cp(str(task_dir / "tests") + "/.", f"{cid}:/tests/")

        if args.oracle:
            log.info("Running oracle solve.sh")
            stdout, stderr, rc = docker_exec(
                cid, "bash /solution/solve.sh", timeout=agent_timeout,
            )
            log.info(f"Oracle rc={rc}")
            if rc != 0:
                log.warning(f"Oracle stderr: {stderr[:300]}")
        else:
            retrieved_names: list[str] = []
            instruction = (task_dir / "instruction.md").read_text()

            LiteLLMProvider = _import_litellm_provider()
            raw_provider = LiteLLMProvider(
                api_key=args.api_key or os.environ.get("ANTHROPIC_API_KEY") or "dummy",
                api_base=args.api_base or None,
                default_model=args.model,
            )
            token_tracker = TokenTracker(raw_provider)
            provider = token_tracker

            _patch_skillhub_retry(timeout_s=30.0, max_retries=3)

            ws = Path(tempfile.mkdtemp(prefix=f"raven_bench_{task_id}_"))
            prompt_injection_mode = bool(
                (args.skill_outputs_dir or getattr(args, 'gold_skill', False)) and not args.no_skills
            )
            try:
                if prompt_injection_mode:
                    if getattr(args, 'gold_skill', False):
                        skill_content, retrieved_names, gold_dirs = load_gold_skills(
                            Path(args.task_dir))
                        retrieved_bodies = []
                    else:
                        gate_fn = None
                        if getattr(args, "gate_plan", None):
                            # Apply a frozen, precomputed gate plan (deterministic:
                            # decisions + rewritten bodies fixed at scan time).
                            import json as _json
                            _plan = _json.load(open(args.gate_plan))
                            _entry = _plan.get(task_id, {})
                            _skills = _entry.get("skills", [])
                            def gate_fn(cands, _skills=_skills):
                                return _skills
                            log.info(f"gate-plan applied for {task_id}: "
                                     f"actions={_entry.get('actions', {})}")
                        elif getattr(args, "relevance_gate", False):
                            from skill_gate import gate_skills, gate_skills_3way
                            _gate_base = args.gate_api_base or args.api_base
                            _gate = gate_skills_3way if getattr(args, "gate_rewrite", False) else gate_skills
                            def gate_fn(cands, _instr=instruction, _gate=_gate):
                                return _gate(
                                    _instr, cands,
                                    api_base=_gate_base,
                                    api_key=args.api_key or os.environ.get("ANTHROPIC_API_KEY") or "dummy",
                                    model=args.gate_model_run or args.model,
                                    log=log,
                                )
                        skill_content, retrieved_names, retrieved_bodies = load_preretrieved_skills(
                            Path(args.skill_outputs_dir), task_id,
                            gate_model=args.gate_model, inject_max=args.inject_max,
                            mass_library_db=Path(args.mass_library_db) if args.mass_library_db else None,
                            gate_fn=gate_fn,
                        )

                    AgentLoop = _import_agent_loop()
                    SessionManager = _import_session_manager()
                    agent_loop = AgentLoop(
                        provider=provider, workspace=ws,
                        model=args.model, max_iterations=args.max_turns,
                        session_manager=SessionManager(ws),
                        interactive=False,
                    )
                    agent_loop.context.skills.get_always_skills = lambda: []
                    agent_loop.context.skills.build_skills_summary = lambda only=None: ""
                    agent_loop.context.skills.load_skills_for_context = lambda *a, **kw: ""

                    if skill_content:
                        instruction = instruction + (
                            "\n\n---\n\n## Active Skills\n\n"
                            "The following skills provide domain knowledge for this task:\n\n"
                            + skill_content
                        )
                        n_att = 0
                        if getattr(args, 'gold_skill', False) and retrieved_names:
                            n_att = inject_gold_skill_attachments(
                                cid, retrieved_names, gold_dirs)
                            log.info(f"Gold skill attachments injected: {n_att} file(s)")
                        elif retrieved_names and args.mass_library_db:
                            n_att = inject_skill_attachments(
                                cid, list(zip(retrieved_names, retrieved_bodies)),
                                str(args.mass_library_db), log=log,
                            )
                            log.info(f"Skill attachments injected into container: {n_att} file(s)")
                        if n_att:
                            instruction = instruction + skill_resources_note("/skills")
                    log.info(f"Prompt-injection mode ({('gold' if getattr(args, 'gold_skill', False) else 'pre-retrieved')}): "
                             f"{len(retrieved_names)} skills: {retrieved_names}")
                elif args.raven_config:
                    agent_loop = get_agent_loop(
                        Path(args.raven_config), provider, args.model,
                        args.max_turns, workspace=ws,
                    )
                elif args.skill_pool:
                    from types import SimpleNamespace
                    config_kwargs = dict(
                        enabled=True,
                        reranker_enabled=args.reranker,
                        top_k=args.top_k,
                        skills_dir=str(args.skill_pool),
                        embedding_url=args.embedding_url,
                        reranker_url=args.reranker_url,
                        embedding_api_key=args.remote_api_key,
                        reranker_api_key=args.remote_api_key,
                    )
                    if args.embedding_model:
                        config_kwargs["embedding_model"] = args.embedding_model
                    if args.reranker_model:
                        config_kwargs["reranker_model"] = args.reranker_model
                    if args.embedding_dimensions:
                        config_kwargs["embedding_dimensions"] = args.embedding_dimensions
                    if args.mass_library_db:
                        config_kwargs["mass_library_db"] = str(args.mass_library_db)
                    sf_config = SimpleNamespace(**config_kwargs)

                    AgentLoop = _import_agent_loop()
                    SessionManager = _import_session_manager()
                    agent_loop = AgentLoop(
                        provider=provider,
                        workspace=ws,
                        model=args.model,
                        max_iterations=args.max_turns,
                        skill_forge_config=sf_config,
                        session_manager=SessionManager(ws),
                        interactive=False,
                    )
                else:
                    AgentLoop = _import_agent_loop()
                    SessionManager = _import_session_manager()

                    sf_router_config = _build_skill_hub_router_config(args)

                    sf_config = None
                    if not args.no_skills:
                        from raven.config.raven import SkillForgeConfig
                        sf_config = SkillForgeConfig(
                            enabled=True,
                            injection_mode="full_body",
                            inject_max=getattr(args, "inject_max", 5),
                            llm_gate_enabled=True,
                            llm_gate_max_select=2,
                            llm_gate_model=getattr(args, "gate_model", None) or None,
                            rewrite_enabled=True,
                        )
                        if sf_router_config is not None:
                            sf_config.router = sf_router_config

                    if getattr(args, "gate_api_base", None) and sf_config and sf_config.llm_gate_enabled:
                        import raven.context_engine.factory as _ce_factory
                        _orig_build_rg = _ce_factory._build_rewriter_and_gate
                        LiteLLMProvider = _import_litellm_provider()
                        _gate_provider = LiteLLMProvider(
                            api_key=args.api_key or os.environ.get("ANTHROPIC_API_KEY") or "dummy",
                            api_base=args.gate_api_base,
                            default_model=sf_config.llm_gate_model or args.model,
                        )
                        def _patched_build_rg(*, provider: "Any", **kw):
                            return _orig_build_rg(provider=_gate_provider, **kw)
                        _ce_factory._build_rewriter_and_gate = _patched_build_rg

                    agent_loop = AgentLoop(
                        provider=provider,
                        workspace=ws,
                        model=args.model,
                        max_iterations=args.max_turns,
                        skill_forge_router_config=sf_router_config,
                        skill_forge_config=sf_config,
                        session_manager=SessionManager(ws),
                        interactive=False,
                    )

                    if args.no_skills:
                        log.info("Running without any skills (--no-skills)")
                        agent_loop.context.skills.get_always_skills = lambda: []
                        agent_loop.context.skills.build_skills_summary = lambda only=None: ""
                        agent_loop.context.skills.load_skills_for_context = lambda *a, **kw: ""

                if not prompt_injection_mode and not args.no_skills:
                    agent_loop.context.skills.get_always_skills = lambda: []

                agent_loop.tools = ToolRegistry()
                agent_loop.tools.register(DockerExecTool(cid, default_timeout=120, working_dir="/app"))
                agent_loop.tools.register(DockerReadFileTool(cid))
                agent_loop.tools.register(TaskCompleteTool())

                log.info(f"Running agent with model={args.model}, max_turns={args.max_turns}")
                t0 = time.time()
                session_key = f"bench:{task_id}"
                final_content = await _run_turn_text(
                    agent_loop, instruction,
                    session_key=session_key, chat_id=task_id,
                )
                agent_time = time.time() - t0

                if not prompt_injection_mode:
                    injected = getattr(agent_loop, "_last_injected_skill_ids", None) or []
                    if injected:
                        retrieved_names = injected
                        log.info(f"Context engine injected skills: {injected}")

                session = agent_loop.sessions.get_or_create(session_key)
                messages = session.messages
                n_tool_calls = sum(
                    len(m.get("tool_calls") or [])
                    for m in messages if m.get("role") == "assistant"
                )
                log.info(f"Agent finished in {agent_time:.1f}s, {n_tool_calls} tool calls")
            finally:
                try:
                    await agent_loop.close_mcp()
                except Exception:
                    pass
                shutil.rmtree(ws, ignore_errors=True)

        # Run verifier
        log.info("Running verifier")
        _inject_offline_deps(cid, task_dir)
        docker_exec(cid, "chmod +x /tests/test.sh", user="root")
        verifier_env = (
            f"export UV_INDEX_URL='{PIP_MIRROR}' UV_DEFAULT_INDEX='{PIP_MIRROR}' UV_INDEX='{PIP_MIRROR}'"
        )
        stdout, stderr, rc = docker_exec(
            cid, f"{verifier_env} && /tests/test.sh > /logs/verifier/test-stdout.txt 2>&1",
            timeout=verifier_timeout,
        )
        log.info(f"Verifier rc={rc}")

        # Read reward
        tmpdir = tempfile.mkdtemp(prefix="_rv_reward_")
        try:
            subprocess.run(
                ["docker", "cp", f"{cid}:/logs/verifier/reward.txt", f"{tmpdir}/reward.txt"],
                timeout=10, check=False, capture_output=True,
            )
            reward_path = Path(tmpdir) / "reward.txt"
            if reward_path.exists() and reward_path.stat().st_size > 0:
                reward = float(reward_path.read_text().strip())
            else:
                subprocess.run(
                    ["docker", "cp", f"{cid}:/logs/verifier/reward.json", f"{tmpdir}/reward.json"],
                    timeout=10, check=False, capture_output=True,
                )
                rj = Path(tmpdir) / "reward.json"
                if rj.exists() and rj.stat().st_size > 0:
                    reward = json.loads(rj.read_text())
                else:
                    reward = 0.0
                    log.warning("No reward file found, defaulting to 0.0")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        # Save results
        output_dir = Path(args.output_dir) / f"raven-{task_id}" / datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir.mkdir(parents=True, exist_ok=True)

        subprocess.run(
            ["docker", "cp", f"{cid}:/logs/verifier/test-stdout.txt", str(output_dir / "test-stdout.txt")],
            timeout=10, check=False, capture_output=True,
        )
        subprocess.run(
            ["docker", "cp", f"{cid}:/logs/verifier/ctrf.json", str(output_dir / "ctrf.json")],
            timeout=10, check=False, capture_output=True,
        )

        token_usage = token_tracker.get_usage() if not args.oracle else {}
        result = {
            "task": task_id,
            "agent": "raven",
            "model": args.model if not args.oracle else "oracle",
            "reward": reward,
            "n_tool_calls": n_tool_calls if not args.oracle else 0,
            "agent_time": agent_time if not args.oracle else None,
            "retrieval": bool(args.skill_pool) if not args.oracle else False,
            "retrieved_skills": [
                (s.name if hasattr(s, 'name') else str(s))
                for s in retrieved_names
            ] if not args.oracle else [],
            "token_usage": token_usage,
            "timestamp": datetime.now().isoformat(),
        }
        (output_dir / "result.json").write_text(json.dumps(result, indent=2))

        if not args.oracle and messages:
            (output_dir / "trajectory.json").write_text(
                json.dumps(messages, indent=2, ensure_ascii=False, default=str)
            )

        print(f"\nTask: {task_id}")
        print(f"Agent: {'oracle' if args.oracle else 'raven'}")
        print(f"Model: {result['model']}")
        print(f"Reward: {reward}")
        if not args.oracle:
            print(f"Tool calls: {n_tool_calls}")
            print(f"Time: {agent_time:.1f}s")
            print(f"Tokens: prompt={token_usage.get('prompt_tokens',0)}, completion={token_usage.get('completion_tokens',0)}, total={token_usage.get('total_tokens',0)}")
        print(f"Results: {output_dir}")

        return result

    finally:
        if not args.no_cleanup:
            stop_container(container_name)
            log.info(f"Container {container_name} removed")


def main():
    parser = argparse.ArgumentParser(description="Run skillsbench task with raven")
    parser.add_argument("task_dir", type=Path, help="Task directory")
    parser.add_argument("--model", "-m", required=True,
                        help="LLM model name (any litellm-compatible id)")
    parser.add_argument("--max-turns", type=int, default=40, help="Max agent iterations")
    parser.add_argument("--oracle", action="store_true", help="Run oracle solution instead")
    parser.add_argument("--no-cleanup", action="store_true", help="Keep container after run")
    parser.add_argument("--api-key", help="LLM API key (default: from env)")
    parser.add_argument("--api-base", help="LLM API base URL for custom endpoints")
    parser.add_argument("--raven-config", type=str, default=None,
                        help="Path to raven config JSON file.")
    parser.add_argument("--skill-pool", type=Path,
                        help="Skill pool workspace (filesystem layout).")
    parser.add_argument("--no-skills", action="store_true",
                        help="Run without any skills (no retrieval, no task-bundled skills).")
    parser.add_argument("--gold-skill", action="store_true",
                        help="Use gold skills from task's environment/skills/ instead of pre-retrieved.")
    parser.add_argument("--skill-outputs-dir", type=str,
                        default=None,
                        help="Pre-retrieved skill outputs dir (prompt-injection mode).")
    parser.add_argument("--gate-model", type=str, default="qwen/qwen3.5-397b-a17b",
                        help="Gate model subdir for --skill-outputs-dir (default: qwen/qwen3.5-397b-a17b).")
    parser.add_argument("--relevance-gate", action="store_true",
                        help="Run a run-time LLM relevance gate over pre-retrieved skills, "
                             "dropping ones judged irrelevant to the task before injection.")
    parser.add_argument("--gate-rewrite", action="store_true",
                        help="Upgrade the relevance gate to 3-way (keep/rewrite/drop): "
                             "partially-relevant skills get task-adapted rewrites instead of "
                             "being kept as-is or dropped.")
    parser.add_argument("--gate-plan", type=str, default=None,
                        help="Apply a precomputed gate plan JSON (per-task final skill set "
                             "with frozen keep/rewrite/drop decisions). Deterministic; takes "
                             "precedence over --relevance-gate.")
    parser.add_argument("--gate-model-run", type=str, default=None,
                        help="Model to use as the relevance-gate judge (default: --model).")
    parser.add_argument("--top-k", type=int, default=5, help="Number of skills to retrieve (default: 5)")
    parser.add_argument("--reranker", action="store_true", help="Enable reranker after embedding retrieval")
    parser.add_argument("--embedding-url", help="Remote embedding service URL")
    parser.add_argument("--reranker-url", help="Remote reranker service URL")
    parser.add_argument("--remote-api-key", help="API key for remote embedding/reranker service")
    parser.add_argument("--embedding-model", help="Override embedding model name")
    parser.add_argument("--reranker-model", help="Override reranker model name")
    parser.add_argument("--mass-library-db", type=Path, default=None,
                        help="SQLite mass-library DB path (skill body lookup + dense retrieval). "
                             "The batch script passes this explicitly.")
    parser.add_argument("--embedding-dimensions", type=int, default=None,
                        help="Request specific embedding dimensions")
    parser.add_argument("--injection-mode", default="summary",
                        choices=["summary", "full_body"],
                        help="Skill injection mode: summary (XML directory) or full_body (inline skill content)")
    parser.add_argument("--inject-max", type=int, default=5,
                        help="Max skills to inline (prompt-injection / full_body mode; default: 5)")
    parser.add_argument("--gate-api-base", type=str, default=None,
                        help="Separate API base URL for the LLM gate model.")
    parser.add_argument("--skill-hub-endpoint", type=str, default=None,
                        help="Skill Hub remote endpoint URL.")
    parser.add_argument("--skill-hub-api-key", type=str, default=None,
                        help="Bearer token for the Skill Hub endpoint.")
    parser.add_argument("--output-dir", "-o", default="jobs", help="Output directory")
    parser.add_argument("--container-prefix", default="raven-bench",
                        help="Docker container/image name prefix (default: raven-bench)")
    args = parser.parse_args()

    if not args.task_dir.exists():
        print(f"Error: {args.task_dir} not found")
        sys.exit(1)

    asyncio.run(run_task(args.task_dir, args))


if __name__ == "__main__":
    main()
