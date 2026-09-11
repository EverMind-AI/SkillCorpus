/**
 * One side of the differential. See README.md — this is run against the
 * output of `_py_side.py` on the same inputs.
 */

import { readFileSync, mkdtempSync, mkdirSync, writeFileSync, utimesSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import * as pv from '../../engine-typescript/src/provenance.ts'
import * as sh from '../../engine-typescript/src/shared.ts'
import * as wt from '../../engine-typescript/src/watch.ts'
import { loadConfig as ocLoadConfig } from '../../plugin-openclaw/src/config.ts'

const py = JSON.parse(readFileSync(process.argv[2] ?? 'py.json', 'utf8'))
const out: Record<string, unknown> = {}
out.identity = [sh.HOME_ENV, pv.identity('hub','x'), pv.identity(' hub ','  y '), pv.identity('','')]
out.body_digest = [pv.bodyDigest(''), pv.bodyDigest('abc'), pv.bodyDigest('中文\n'), pv.bodyDigest('🚀')]
const n = pv.now()
out.now_shape = [n.length, n[4], n[10], n.slice(-6)]
out.opted_in = [null, true, false, 1, 0, 'true','True','TRUE','false','0','no','off','yes','on','','  ','banana',2,-1,0.0,1.5]
  .map(v => sh.optedIn(v))

// fingerprint
const d = mkdtempSync(join(tmpdir(),'fp-'))
mkdirSync(join(d,'a')); writeFileSync(join(d,'a','SKILL.md'),'x')
const t = new Date(1234567890123.456789)
utimesSync(join(d,'a','SKILL.md'), t, t)
out.fingerprint_shape = wt.fingerprint([d]).replace(d,'<ROOT>').replace(/:\d+$/,':<NS>')

// 交叉读：Python 写的 registry / marker，TS 能不能一模一样地读出来
out.reads_python_registry = sh.readRegistry(py.registry_path)
out.reads_python_marker = pv.readMarker(py.marker_path)
// TS 重写一遍，字节要和 Python 写的一致
sh.registerHost('raven', sh.readRegistry(py.registry_path)[0].dir, py.registry_path)
out.registry_after_ts_rewrite = readFileSync(py.registry_path,'utf8')
const m2 = mkdtempSync(join(tmpdir(),'mk-'))
pv.writeMarker(m2, { origin: pv.identity('hub','s'), source:'hub', slug:'s', version:'1.0',
                     sha256: pv.bodyDigest('b'), installedAt:'2026-01-01T00:00:00+00:00' })
out.marker_written = readFileSync(join(m2, pv.MARKER),'utf8')
// Environment precedence, including the whitespace case the ports disagreed
// on: a variable holding only spaces must read as unset, not as an override
// that empties the list.
out.env_precedence = [{}, { SKILLSEARCH_SKILLS_DIRS: '' }, { SKILLSEARCH_SKILLS_DIRS: '   ' },
                      { SKILLSEARCH_SKILLS_DIRS: '\t\n' }, { SKILLSEARCH_SKILLS_DIRS: '/e1,/e2' }]
  .map(env => ocLoadConfig({ skillsDirs: ['/cfg'] }, env).skillsDirs)
console.log(JSON.stringify(out))
