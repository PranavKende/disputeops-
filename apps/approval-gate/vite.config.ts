import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// base: './' is required — the UiPath platform handles URL routing at runtime.
export default defineConfig({
  plugins: [react()],
  base: './',
});
