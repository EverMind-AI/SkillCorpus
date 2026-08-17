"""What the engine promises, independent of any host."""

from pathlib import Path

from skillsearch import SearchConfig, SkillSearch


def _skill(root: Path, name: str, description: str, body: str = "Body.") -> None:
    d = root / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n")


class TestConfig:
    def test_defaults_are_a_working_local_setup(self) -> None:
        cfg = SearchConfig()
        assert cfg.skills_dir and cfg.top_k > 0

    def test_from_mapping_coerces_strings(self) -> None:
        cfg = SearchConfig.from_mapping({"top_k": "7", "rewrite": "false", "hub_timeout_s": "30"})
        assert cfg.top_k == 7
        assert cfg.rewrite is False
        assert cfg.hub_timeout_s == 30.0

    def test_unknown_keys_are_ignored(self) -> None:
        # Hosts hand over a whole slice; a key this version predates is a
        # forward-compatibility case, not an error.
        cfg = SearchConfig.from_mapping({"top_k": 3, "from_a_future_version": True})
        assert cfg.top_k == 3

    def test_capability_follows_presence_not_flags(self, tmp_path: Path) -> None:
        _skill(tmp_path, "a", "d")
        s = SkillSearch(SearchConfig(skills_dir="skills", workspace=str(tmp_path)))
        assert s._gate is None and s._rewriter is None  # no model configured


class TestRetrieval:
    async def test_local_skill_is_found_and_rendered(self, tmp_path: Path) -> None:
        _skill(tmp_path, "pdf-tables", "Extract tables from PDF files.", "Run the script.")
        s = SkillSearch(SearchConfig(skills_dir="skills", workspace=str(tmp_path)))
        out = await s.retrieve("extract tables from a pdf")
        assert "# Skills" in out
        assert "pdf-tables" in out
        assert "Run the script." in out

    async def test_no_sources_returns_empty(self, tmp_path: Path) -> None:
        s = SkillSearch(SearchConfig(skills_dir="absent", hub_endpoint="", workspace=str(tmp_path)))
        assert await s.retrieve("anything") == ""

    async def test_blank_query_returns_empty(self, tmp_path: Path) -> None:
        _skill(tmp_path, "a", "d")
        s = SkillSearch(SearchConfig(skills_dir="skills", workspace=str(tmp_path)))
        assert await s.retrieve("   ") == ""

    async def test_retrieve_never_raises(self, tmp_path: Path) -> None:
        # The hot-path contract: a broken source costs the turn its skills,
        # never the turn itself.
        _skill(tmp_path, "a", "d")
        s = SkillSearch(SearchConfig(skills_dir="skills", workspace=str(tmp_path)))

        async def _boom(*a, **k):
            raise RuntimeError("source exploded")

        s._router.select = _boom
        assert await s.retrieve("anything") == ""

    async def test_hits_returns_records(self, tmp_path: Path) -> None:
        _skill(tmp_path, "pdf-tables", "Extract tables from PDF files.")
        s = SkillSearch(SearchConfig(skills_dir="skills", workspace=str(tmp_path)))
        hits = await s.hits("extract tables from a pdf")
        assert hits and hits[0].name == "pdf-tables"


class TestLocalStore:
    def test_frontmatter_is_parsed(self, tmp_path: Path) -> None:
        from skillsearch.local_store import DirectorySkillStore

        _skill(tmp_path, "demo", "A demo skill.", "The body.")
        store = DirectorySkillStore([(tmp_path / "skills", "local")])
        skills = store.list_all()
        assert len(skills) == 1
        assert skills[0].name == "demo"
        assert skills[0].description == "A demo skill."
        assert "The body." in skills[0].content

    def test_extra_roots_are_scanned(self, tmp_path: Path) -> None:
        from skillsearch.local_store import DirectorySkillStore

        _skill(tmp_path, "a", "d")
        other = tmp_path / "team"
        (other / "b").mkdir(parents=True)
        (other / "b" / "SKILL.md").write_text("---\nname: b\ndescription: d\n---\n\nx\n")
        store = DirectorySkillStore([(tmp_path / "skills", "local"), (other, "team")])
        assert {s.name for s in store.list_all()} == {"a", "b"}


class _ScriptedModel:
    """A ChatModel that answers by contract, recording what it was asked.

    Lets the gate's behaviour be asserted without a network call: the
    rewriter and the gate are told apart by what their prompts contain.
    """

    def __init__(self, keep: str = "", *, gate_raises: bool = False, gate_delay: float = 0.0) -> None:
        self.keep = keep
        self.gate_raises = gate_raises
        self.gate_delay = gate_delay
        self.prompts: list[str] = []

    async def complete(self, messages, *, model=None, temperature=0.0, max_tokens=8192):
        import asyncio
        import json
        import re

        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        if "Candidate Skills" not in prompt:
            return json.dumps({"need_retrieval": True, "query": "rewritten query"})
        if self.gate_raises:
            raise RuntimeError("gate model is down")
        if self.gate_delay:
            await asyncio.sleep(self.gate_delay)
        ids = re.findall(r"^- (\S+):", prompt, re.M)
        return json.dumps({"plan": "p", "skills": [i for i in ids if self.keep in i]})

    @property
    def gate_prompt(self) -> str:
        return next((p for p in reversed(self.prompts) if "Candidate Skills" in p), "")


def _three_overlapping_skills(root: Path) -> None:
    """Three skills a keyword search returns together, so the gate has
    something to actually narrow."""
    for name, desc in [
        ("pdf-tables", "Extract data tables from documents."),
        ("csv-export", "Extract data into CSV documents."),
        ("doc-scan", "Extract data by scanning documents."),
    ]:
        d = root / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\n---\n\nextract data documents\n",
        )


def _names(block: str) -> list[str]:
    return [line.split("[")[1].rstrip("]") for line in block.splitlines() if line.startswith("### ")]


class TestGate:
    async def test_gate_narrows_what_retrieval_returned(self, tmp_path: Path) -> None:
        _three_overlapping_skills(tmp_path)
        base = {"skills_dir": "skills", "workspace": str(tmp_path), "top_k": 5}

        ungated = await SkillSearch(SearchConfig(**base)).retrieve("extract data from documents")
        assert len(_names(ungated)) == 3

        model = _ScriptedModel(keep="pdf")
        gated = await SkillSearch(
            SearchConfig(**base, model="scripted", max_select=2),
            model=model,
        ).retrieve("extract data from documents")
        assert _names(gated) == ["local/pdf-tables"]

    async def test_gate_sees_the_agents_tools(self, tmp_path: Path) -> None:
        # The gate drops skills needing a tool the agent lacks, so the tool
        # names have to reach its prompt.
        _three_overlapping_skills(tmp_path)
        model = _ScriptedModel(keep="pdf")
        await SkillSearch(
            SearchConfig(skills_dir="skills", workspace=str(tmp_path), model="scripted"),
            model=model,
            get_tools=lambda: [{"function": {"name": "exec"}}, "read_file"],
        ).retrieve("extract data from documents")
        assert "exec" in model.gate_prompt
        assert "read_file" in model.gate_prompt

    async def test_gate_failure_keeps_the_top_hits(self, tmp_path: Path) -> None:
        _three_overlapping_skills(tmp_path)
        out = await SkillSearch(
            SearchConfig(skills_dir="skills", workspace=str(tmp_path), model="scripted", max_select=2),
            model=_ScriptedModel(gate_raises=True),
        ).retrieve("extract data from documents")
        assert len(_names(out)) == 2  # degraded, not empty

    async def test_slow_gate_is_bounded(self, tmp_path: Path) -> None:
        # The gate is one LLM call on the turn's hot path; exceeding its
        # deadline must cost the turn a filter, not its response.
        _three_overlapping_skills(tmp_path)
        out = await SkillSearch(
            SearchConfig(
                skills_dir="skills",
                workspace=str(tmp_path),
                model="scripted",
                max_select=1,
                gate_timeout_s=0.05,
            ),
            model=_ScriptedModel(keep="pdf", gate_delay=1.0),
        ).retrieve("extract data from documents")
        assert len(_names(out)) == 1

    async def test_rewriter_and_gate_are_both_called(self, tmp_path: Path) -> None:
        _three_overlapping_skills(tmp_path)
        model = _ScriptedModel(keep="pdf")
        await SkillSearch(
            SearchConfig(skills_dir="skills", workspace=str(tmp_path), model="scripted"),
            model=model,
        ).retrieve("extract data from documents")
        assert len(model.prompts) == 2
        assert model.gate_prompt

    async def test_engine_speaks_the_port_not_a_host_api(self, tmp_path: Path) -> None:
        # Regression: gate and rewriter once called Raven's
        # chat_with_retry(), which fails silently on any other host and
        # degrades retrieval to unfiltered top-N.
        _three_overlapping_skills(tmp_path)
        model = _ScriptedModel(keep="pdf")
        await SkillSearch(
            SearchConfig(skills_dir="skills", workspace=str(tmp_path), model="scripted"),
            model=model,
        ).retrieve("extract data")
        assert model.prompts, "the engine never called ChatModel.complete()"


class TestCacheLocation:
    def test_bundle_cache_sits_outside_the_scanned_tree(self, tmp_path: Path) -> None:
        # Downloaded bundles must not land where the local scanner walks:
        # a remote skill would come back next turn as a local-looking copy
        # of itself, competing with the original in the same ranking.
        cfg = SearchConfig(skills_dir="skills", workspace=str(tmp_path))
        skills = cfg.resolved_skills_dir()
        cache = cfg.resolved_cache_dir()
        assert skills is not None
        assert not str(cache).startswith(str(skills) + "/")
        assert cache != skills

    def test_explicit_cache_dir_is_honoured(self, tmp_path: Path) -> None:
        cfg = SearchConfig(cache_dir=str(tmp_path / "elsewhere"), workspace=str(tmp_path))
        assert cfg.resolved_cache_dir() == tmp_path / "elsewhere"

    def test_downloaded_bundles_are_not_rescanned(self, tmp_path: Path) -> None:
        from skillsearch.local_store import DirectorySkillStore

        # Simulate a bundle that was extracted into the default cache.
        cfg = SearchConfig(skills_dir="skills", workspace=str(tmp_path))
        skills = cfg.resolved_skills_dir()
        (skills / "mine").mkdir(parents=True)
        (skills / "mine" / "SKILL.md").write_text("---\nname: mine\ndescription: d\n---\n\nx\n")
        pulled = cfg.resolved_cache_dir() / "remote@v1" / "remote"
        pulled.mkdir(parents=True)
        (pulled / "SKILL.md").write_text("---\nname: remote\ndescription: d\n---\n\nx\n")

        store = DirectorySkillStore([(skills, "local")])
        assert {s.name for s in store.list_all()} == {"mine"}
