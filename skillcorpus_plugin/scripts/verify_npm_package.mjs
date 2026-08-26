import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const packageDir = resolve(process.argv[2] ?? '.')
const pkg = JSON.parse(readFileSync(resolve(packageDir, 'package.json'), 'utf8'))
const packed = JSON.parse(execFileSync('npm', ['pack', '--dry-run', '--json'], {
  cwd: packageDir,
  encoding: 'utf8',
}))[0]
const files = new Set(packed.files.map(file => file.path))

for (const required of ['package.json', 'LICENSE']) {
  if (!files.has(required)) throw new Error(`${pkg.name}: tarball is missing ${required}`)
}
for (const field of ['main', 'types']) {
  const target = pkg[field]
  if (!target) continue
  if (!existsSync(resolve(packageDir, target))) throw new Error(`${pkg.name}: ${field} target does not exist: ${target}`)
  if (!files.has(target)) throw new Error(`${pkg.name}: tarball omits ${field} target: ${target}`)
}

const manifest = pkg.name.includes('openclaw')
  ? 'openclaw.plugin.json'
  : pkg.name.includes('workbuddy')
    ? '.codebuddy-plugin/plugin.json'
    : undefined
if (manifest) {
  const data = JSON.parse(readFileSync(resolve(packageDir, manifest), 'utf8'))
  if (data.version !== pkg.version) throw new Error(`${pkg.name}: manifest ${data.version} != package ${pkg.version}`)
  if (!files.has(manifest)) throw new Error(`${pkg.name}: tarball omits ${manifest}`)
}


const forbiddenToken = ['me', 'mmy'].join('')
for (const file of files) {
  const path = resolve(packageDir, file)
  if (!existsSync(path)) continue
  const text = readFileSync(path, 'utf8').toLowerCase()
  if (text.includes(forbiddenToken)) throw new Error(`${pkg.name}: tarball contains a forbidden legacy product name in ${file}`)
}
