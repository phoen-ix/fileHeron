// Build-time env exposed by Vite on `import.meta.env`. Only the vars this app
// actually reads are declared (kept minimal on purpose).
interface ImportMetaEnv {
  /** Byte threshold for the direct-vs-resumable upload split; empty/unset falls
   *  back to the 100 MB default. Set at image build. See useUpload.ts. */
  readonly VITE_DIRECT_UPLOAD_THRESHOLD?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
  /** Vite's glob import. Declared here (rather than pulling in the whole
   *  `vite/client` lib) because the structural tests read source files with it
   *  - a bare `import('/src/x.ts?raw')` type-checks as a missing module even
   *  though vite serves it, which is how a test can pass locally and fail the
   *  `npm run build` gate. Minimal signature: only the eager+raw form is used. */
  glob<T = unknown>(
    pattern: string | string[],
    options?: {
      query?: string
      import?: string
      eager?: boolean
    },
  ): Record<string, T>
}

// TypeScript 6 (TS2882) requires a declaration for a side-effect import of a
// non-code module. These are the stylesheet imports Vite resolves and injects -
// `import './styles/global.css'` in main.ts and the three ProseMirror
// stylesheets in RichTextEditor.vue. They contribute no bindings, hence the
// empty declarations.
declare module '*.css'
declare module '*.scss'
