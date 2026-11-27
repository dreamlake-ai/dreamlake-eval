declare module '*.mdx' {
  import type { ComponentType } from 'react'
  export interface Frontmatter {
    title?: string
    section?: string
    order?: number
    description?: string
    draft?: boolean
    hidden?: boolean
    noindex?: boolean
    /** Right-rail TOC depth. 3 (default) surfaces H2 + H3; 2 limits
     *  the rail to H2 only — useful on long pages where the H3 count
     *  would crowd the rail. */
    tocLevel?: 2 | 3
    fullscreen?: boolean
  }
  export const frontmatter: Frontmatter
  const Component: ComponentType
  export default Component
}

declare module '*?raw' {
  const content: string
  export default content
}
