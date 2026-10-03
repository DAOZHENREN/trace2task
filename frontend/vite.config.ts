import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: '/workbench/',
  plugins: [react()],
  build: {outDir: '../src/trace2task/web/workbench', emptyOutDir: true, sourcemap: false},
  server: {proxy: {'/api': 'http://127.0.0.1:8765', '/legacy': 'http://127.0.0.1:8765'}},
});
