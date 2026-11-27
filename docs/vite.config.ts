import { defineConfig } from 'vite'
import { readFileSync } from 'node:fs'
import { execSync } from 'node:child_process'
import { dockit } from '@dreamlake/dockit/vite'

/** Captured once at build/dev start. */
const pkg = JSON.parse(
  readFileSync(new URL('./package.json', import.meta.url), 'utf-8'),
) as { version?: string }
const APP_VERSION = pkg.version ?? '0.0.0'
const GIT_HASH = (() => {
  try {
    return execSync('git rev-parse --short=6 HEAD', { encoding: 'utf-8' }).trim()
  } catch {
    return 'dev'
  }
})()

// dockit() bundles the whole docs stack (Tailwind v4, ?raw-MDX loader,
// MDX + shiki, React, Vike). Requires vike >= 0.4.260. Spread (not
// `[dockit()]`): the standalone `vike prerender` CLI only scans the top
// level of `plugins` — nested one array deeper it sometimes injects a
// duplicate Vike plugin and the prerender crashes nondeterministically.
export default defineConfig({
  server: { host: "0.0.0.0", port: 3018 },
  preview: { host: "0.0.0.0", port: 3018 },
  define: {
    __APP_VERSION__: JSON.stringify(APP_VERSION),
    __GIT_HASH__: JSON.stringify(GIT_HASH),
  },
  plugins: [...dockit()],
})
