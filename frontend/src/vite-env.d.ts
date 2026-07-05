// Build-time env exposed by Vite on `import.meta.env`. Only the vars this app
// actually reads are declared (kept minimal on purpose).
interface ImportMetaEnv {
  /** Byte threshold for the direct-vs-resumable upload split; empty/unset falls
   *  back to the 100 MB default. Set at image build. See useUpload.ts. */
  readonly VITE_DIRECT_UPLOAD_THRESHOLD?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
