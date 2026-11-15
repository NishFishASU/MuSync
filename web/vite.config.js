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
				changeOrigin: true,
				// SSE /api/events — disable proxy buffering so EventSource works in npm run dev.
				configure: (proxy) => {
					proxy.on('proxyRes', (proxyRes, req) => {
						if (req.url && req.url.includes('/api/events')) {
							proxyRes.headers['cache-control'] = 'no-cache';
							proxyRes.headers['x-accel-buffering'] = 'no';
						}
					});
				}
			}
		}
	},
	preview: {
		port: 4173,
		proxy: {
			'/api': {
				target: apiTarget,
				changeOrigin: true,
				// SSE /api/events — disable proxy buffering so EventSource works in npm run dev.
				configure: (proxy) => {
					proxy.on('proxyRes', (proxyRes, req) => {
						if (req.url && req.url.includes('/api/events')) {
							proxyRes.headers['cache-control'] = 'no-cache';
							proxyRes.headers['x-accel-buffering'] = 'no';
						}
					});
				}
			}
		}
	},
	build: {
		target: 'es2020',
		cssMinify: true,
		reportCompressedSize: true
	}
});
