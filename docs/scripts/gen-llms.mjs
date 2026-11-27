import { readdir, readFile, mkdir, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { dirname, join } from 'node:path'
const base = new URL('../', import.meta.url)
const pkg = JSON.parse(await readFile(new URL('package.json', base), 'utf8'))
const revision = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim()
const origin = 'https://eval.dreamlake.ai'
const pages = []
async function walk(dir, route = '') {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) await walk(path, route + '/' + entry.name)
    else if (entry.name === '+Page.mdx') {
      const raw = await readFile(path, 'utf8')
      const front = raw.match(/^---\n([\s\S]*?)\n---\n/)
      if (!front) throw new Error(`Missing frontmatter: ${path}`)
      const title = front[1].match(/^title: (.+)$/m)?.[1]
      const body = raw.slice(front[0].length)
      const url = route === '/index' ? '/' : route
      const markdown = `<!-- source: ${origin}${url} @ ${pkg.version} ${revision} -->\n\n${body}`
      const output = new URL('dist/client' + (url === '/' ? '/index' : url) + '.md', base)
      await mkdir(dirname(output.pathname), { recursive: true })
      await writeFile(output, markdown)
      pages.push({ title, url, markdown })
    }
  }
}
await walk(new URL('pages', base).pathname)
pages.sort((a,b) => a.url.localeCompare(b.url))
await writeFile(new URL('dist/client/llms.txt', base), '# DreamLake Eval\n\n' + pages.map(p => `- [${p.title}](${origin}${p.url === '/' ? '/index' : p.url}.md)`).join('\n') + '\n')
await writeFile(new URL('dist/client/llms-full.txt', base), pages.map(p => p.markdown).join('\n---\n'))
await writeFile(new URL('dist/client/release.json', base), JSON.stringify({ version: pkg.version, revision, pages: pages.length }) + '\n')
console.log(`Generated ${pages.length} Markdown pages and LLM indexes.`)
