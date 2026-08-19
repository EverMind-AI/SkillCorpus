"""Regenerate tests/fixtures-zip.json.

Written by Python's `zipfile` on purpose: a ZIP reader tested only against
archives it produced itself proves nothing about the format it claims to
read. Run from this directory:

    python3 make-zip-fixtures.py fixtures-zip.json
"""
import io, sys, zipfile, json
def build(entries, compress=zipfile.ZIP_DEFLATED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compress) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()
cases = {
  "wrapped": build({"pdf-forms/SKILL.md": "---\nname: pdf-forms\ndescription: d\n---\n\nRun {baseDir}/scripts/fill.sh and see [g](references/naming.md).\n",
                    "pdf-forms/scripts/fill.sh": "#!/bin/sh\n",
                    "pdf-forms/references/naming.md": "# naming\n"}),
  "flat": build({"SKILL.md": "# flat\n", "notes.txt": "x\n"}),
  "traversal": build({"SKILL.md": "ok\n", "../escape.md": "pwned\n"}),
  "disallowed": build({"SKILL.md": "ok\n", "run.exe": "MZ"}),
  "stored": build({"SKILL.md": "stored\n"}, zipfile.ZIP_STORED),
}
import base64, pathlib
pathlib.Path(sys.argv[1]).write_text(json.dumps({k: base64.b64encode(v).decode() for k, v in cases.items()}))
print("造好", len(cases), "个 zip")
