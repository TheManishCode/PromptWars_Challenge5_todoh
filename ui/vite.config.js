import { defineConfig } from 'vite';

// The build lands in the Flask static folder under flat, fixed names (plan.js, plan.css, plan-<art>.webp):
// the server serves a whitelist of names, not a directory, and the page is given a ?v= address by the server,
// so no content hash is needed in the file name.
export default defineConfig({
  base: '/static/',
  publicDir: false,
  oxc: { jsx: { runtime: 'automatic' } },
  build: {
    outDir: '../eventmate/web/static',
    emptyOutDir: false, // the folder holds the landing page and the dashboard too
    cssCodeSplit: false,
    assetsInlineLimit: 0,
    modulePreload: false,
    rollupOptions: {
      input: 'src/main.jsx',
      output: {
        entryFileNames: 'plan.js',
        chunkFileNames: 'plan-[name].js',
        assetFileNames: (asset) => (asset.names?.[0]?.endsWith('.css') ? 'plan.css' : 'plan-[name][extname]'),
        inlineDynamicImports: true,
      },
    },
  },
  server: {
    proxy: { '/api': 'http://127.0.0.1:8765' },
  },
});
