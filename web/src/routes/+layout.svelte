<script>
	import { page } from '$app/stores';
	import '../app.css';
	import HubView from '$lib/components/HubView.svelte';

	/** @type {{ children: import('svelte').Snippet }} */
	let { children } = $props();

	/** @param {string} pathname */
	function sectionFromPath(pathname) {
		const p = (pathname || '/').replace(/\/+$/, '') || '/';
		if (p === '/') return /** @type {const} */ ('now');
		const key = p.startsWith('/') ? p.slice(1) : p;
		const known = new Set([
			'outputs',
			'inputs',
			'streaming',
			'queue',
			'wifi',
			'profiles'
		]);
		if (known.has(key)) return /** @type {'outputs' | 'inputs' | 'streaming' | 'queue' | 'wifi' | 'profiles'} */ (key);
		return /** @type {const} */ ('now');
	}

	const section = $derived(sectionFromPath($page.url.pathname));
</script>

<HubView {section} />
<!-- Route pages exist for prerender / SPA fallback; shell owns the UI. -->
<div hidden aria-hidden="true">{@render children()}</div>
