import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

const apiTarget = process.env.MUSYNC_API_PROXY || 'http://127.0.0.1:8080';

export default defineConfig({
	plugins: [sveltekit()],
	server: {
		port: 5173,
		strictPort: true,
		proxy: {
			'/api': {
				target: apiTarget,
				changeOrigin: true
			}
		}
	},
	preview: {
		port: 4173,
		proxy: {
			'/api': {
				target: apiTarget,
				changeOrigin: true
			}
		}
	},
	build: {
		target: 'es2020',
		cssMinify: true,
		reportCompressedSize: true
	}
});
