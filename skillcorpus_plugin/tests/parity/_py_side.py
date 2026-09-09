"""One side of the differential. See README.md — the output is compared
against `_ts_side.ts` run on the same inputs."""

import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine-python"))
from skillsearch import provenance as pv, shared as sh, watch as wt

out = {}
out["identity"] = [sh.HOME_ENV, pv.identity("hub","x"), pv.identity(" hub ","  y "), pv.identity("","")]
out["body_digest"] = [pv.body_digest(""), pv.body_digest("abc"), pv.body_digest("中文\n"), pv.body_digest("🚀")]
out["now_shape"] = len(pv.now()), pv.now()[4], pv.now()[10], pv.now()[-6:]
out["opted_in"] = [sh.opted_in(v) for v in
    [None, True, False, 1, 0, "true","True","TRUE","false","0","no","off","yes","on","", "  ", "banana", 2, -1, 0.0, 1.5]]
out["configured_dirs"] = [
    sh.configured_dirs(None, {}), sh.configured_dirs("", {}),
    sh.configured_dirs("/a, /b ,, /c", {}), sh.configured_dirs(["/a"," /b ",""], {}),
    sh.configured_dirs("/x", {"SKILLSEARCH_SKILLS_DIRS":"/env1,/env2"}),
    sh.configured_dirs("/x", {"SKILLSEARCH_SKILLS_DIRS":"   "}),
]
# fingerprint 的字符串形状
d = Path(tempfile.mkdtemp())
(d/"a").mkdir(); (d/"a"/"SKILL.md").write_text("x")
os.utime(d/"a"/"SKILL.md", ns=(1234567890123456789, 1234567890123456789))
fp = wt.fingerprint([d])
out["fingerprint"] = fp.replace(str(d), "<ROOT>")
# registry 往返：Python 写
reg = Path(tempfile.mkdtemp())/"registry.json"
sh.register_host("raven", d, reg)
sh.register_host("openclaw2", d.parent, reg)
out["registry_written"] = reg.read_text()
out["registry_path"] = str(reg)
# marker 往返：Python 写
m = Path(tempfile.mkdtemp())
pv.write_marker(m, pv.Origin(pv.identity("hub","s"), "hub", "s", "1.0", pv.body_digest("b"), "2026-01-01T00:00:00+00:00"))
out["marker_written"] = (m/pv.MARKER).read_text()
out["marker_path"] = str(m)
out["env_precedence"] = [
    sh.configured_dirs(["/cfg"], {}),
    sh.configured_dirs(["/cfg"], {"SKILLSEARCH_SKILLS_DIRS": ""}),
    sh.configured_dirs(["/cfg"], {"SKILLSEARCH_SKILLS_DIRS": "   "}),
    sh.configured_dirs(["/cfg"], {"SKILLSEARCH_SKILLS_DIRS": "\t\n"}),
    sh.configured_dirs(["/cfg"], {"SKILLSEARCH_SKILLS_DIRS": "/e1,/e2"}),
]
print(json.dumps(out, ensure_ascii=False))
