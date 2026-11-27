import { initDocs } from '@dreamlake/dockit'

const pages = import.meta.glob('./pages/**/+Page.mdx', { eager: true }) as Record<string, { frontmatter?: Record<string, unknown> }>
const rawPages = import.meta.glob('./pages/**/+Page.mdx', { eager: true, query: '?raw', import: 'default' }) as Record<string, string>
initDocs({
  site: {
    brand: 'DreamLake', subtitle: 'Eval', breadcrumbRoot: 'eval',
    repoUrl: 'https://github.com/dreamlake-ai/dreamlake-eval',
    docsRepoUrl: 'https://github.com/dreamlake-ai/dreamlake-eval',
    docsBranch: 'main', docsPagesPath: 'docs/pages',
    url: 'https://eval.dreamlake.ai', skill: 'dreamlake-eval',
    summary: 'Evaluation inventory, attempts, measurements and reports across long-running jobs. Pytest is the first adapter; Dash records metrics and artifacts.',
    versionChips: [{ label: 'eval', version: __APP_VERSION__ }],
    gitHash: __GIT_HASH__, themeToggle: 'segmented',
  },
  pages, rawPages,
  sectionOrder: ['Start', 'Reports', 'Design notes'],
})
