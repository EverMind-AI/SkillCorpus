#!/usr/bin/env python3
"""Load the plugin the way Raven loads it, and build the `# Skills` segment.

Goes through the host's own registry: `build_plugin_registry` discovers the
entry point, `build_plugin_segments` constructs the segment that claimed the
`skills` stage, and `build()` produces what Raven puts in the prompt.

Raven is not a dependency of this repository, so point `RAVEN_ROOT` at a
checkout (or pass it as the first argument). Without one the script says so
and exits 0 rather than failing a suite that cannot run it — the packaged
tests in `plugin-raven/tests` skip on the same condition.
"""
import asyncio, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAVEN_ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("RAVEN_ROOT", ""))
if not (RAVEN_ROOT and (RAVEN_ROOT / "raven").is_dir()):
    print("需要一个 Raven checkout：设置 RAVEN_ROOT 或作为第一个参数传入；跳过。")
    raise SystemExit(0)

sys.path.insert(0, str(RAVEN_ROOT))
sys.path.insert(0, str(ROOT / "engine-python"))
sys.path.insert(0, str(ROOT / "plugin-raven"))

from raven.config import RavenConfig
from raven.cli._plugin_stack import build_plugin_registry, build_plugin_segments


def write_skill(root: Path, name: str, description: str, body: str) -> None:
    (root / name).mkdir(parents=True, exist_ok=True)
    (root / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n", encoding="utf-8"
    )


ws = Path(tempfile.mkdtemp(prefix="verify-raven-"))
skills = ws / "skills"
write_skill(skills, "pdf-forms", "Fill PDF acroforms", "Run pdftk with an FDF.")
write_skill(skills, "git-bisect", "Find the commit that broke a test", "Run git bisect start.")
write_skill(skills, "csv-audit", "Audit a CSV for malformed rows", "Read the header, count fields.")

cfg = RavenConfig()
cfg.plugins.config["skillsearch"] = {"skills_dir": str(skills), "top_k": 1}

registry = build_plugin_registry(cfg)
assert registry.context_segment_plugin_id("skills") == "skillsearch", "the skills stage was not claimed"
print(f"claimed stage   : skills -> {registry.context_segment_plugin_id('skills')}")

segments = build_plugin_segments(ws, cfg, registry=registry)
segment = segments.get("skills")
assert segment is not None, "no segment was built for the skills stage"
print(f"built segment   : {type(segment).__name__}")

# The assembler is the step past `build()`: it sorts every builder by the
# `order` the protocol requires, so a segment missing those attributes
# raises here and the agent never starts — `build()` alone cannot show it.
from raven.agent.context import ContextBuilder
from raven.context_engine.base import TokenBudget
from raven.context_engine.curator import TurnContext
from raven.context_engine.factory import build_context_engine


class _NoModel:
    async def complete(self, *a, **k):
        raise AssertionError("assembling context must not call a model")


engine = build_context_engine(
    workspace=ws, config=cfg.context, builder=ContextBuilder(ws),
    provider=_NoModel(), model="none", context_window_tokens=128000,
    get_tool_definitions=lambda: [], plugin_segments=segments,
)
order = [(getattr(b, "name", "?"), getattr(b, "order", "?")) for b in engine._builders]
assert ("skills", 5) in order, f"the segment did not take the reserved slot: {order}"
print(f"assembled order : {' < '.join(n for n, _ in order)}")

budget = TokenBudget(context_length=128000, reserved_output=8000, reserved_tools=2000,
                     reserved_system=2000, available_history=100000)
assembled = asyncio.run(engine.assemble(
    "verify", [], budget,
    turn=TurnContext(current_message="fill in the acroform in /tmp/a7f2.pdf"),
))
prompt = getattr(assembled, "system_prompt", "") or str(assembled)
assert "# Skills" in prompt and "pdf-forms" in prompt, "the block never reached the prompt"
print("reached prompt  : # Skills, with pdf-forms")

failures = 0
for query, expected in [
    ("fill in the acroform in /tmp/a7f2.pdf", "pdf-forms"),
    ("which change broke the test", "git-bisect"),
    ("audit this csv for bad rows", "csv-audit"),
    ("kubernetes ingress annotations", None),
]:
    ctx = type("Ctx", (), {"current_message": query, "session_messages": []})()
    out = asyncio.run(segment.build(ctx))
    text = "" if out is None else (getattr(out, "content", None) or getattr(out, "text", "") or str(out))
    got = text.split("### Skill: ")[1].split(" ")[0] if "### Skill: " in text else None
    ok = got == expected
    failures += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  {query[:40]:42} 期望 {(expected or '(nothing)'):12} 实得 {got or '(nothing)'}")

# Exit hard. Every assertion above has run and reported; what is being
# skipped is interpreter teardown, which on this machine segfaults for the
# host alone — `ContextBuilder` plus `build_plugin_registry`, with
# skillsearch uninstalled, is enough to reproduce it. Letting the process
# unwind would report a host-environment crash as this plugin's failure.
sys.stdout.flush()
os._exit(1 if failures else 0)
