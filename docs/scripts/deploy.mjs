import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
if (process.env.DREAMLAKE_EVAL_DOCS_LOCKED !== '1') {
  execFileSync('flock', ['/tmp/dreamlake-eval-docs-publication.lock', process.execPath, fileURLToPath(import.meta.url)], { stdio: 'inherit', env: { ...process.env, DREAMLAKE_EVAL_DOCS_LOCKED: '1' } })
  process.exit(0)
}
const root = fileURLToPath(new URL('../../', import.meta.url))
const docs = fileURLToPath(new URL('../', import.meta.url))
const git = (...args) => execFileSync('git', args, { cwd: root, encoding: 'utf8' }).trim()
if (git('status', '--porcelain')) throw new Error('Commit or discard working changes before publishing docs')
const revision = git('rev-parse', 'HEAD')
git('fetch', 'origin', 'main')
git('merge-base', '--is-ancestor', revision, 'origin/main')
execFileSync('pnpm', ['build'], { cwd: docs, stdio: 'inherit' })
const release = JSON.parse(readFileSync(`${docs}/dist/client/release.json`, 'utf8'))
if (release.revision !== revision) throw new Error('Built revision does not match the committed source')
// Infrastructure is Terraform-owned. This command publishes files to the existing site only.
execFileSync('netlify', ['deploy', '--prod', '--no-build', '--dir', 'dist/client', '--site', '43133682-8e38-44a4-8592-a8c8f6c7c9be', '--message', `eval-docs ${revision}`], { cwd: docs, stdio: 'inherit' })
console.log(`Published documentation source ${revision}; verify https://eval.dreamlake.ai/release.json`)
