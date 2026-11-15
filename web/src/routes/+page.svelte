<script>
	import { onMount } from 'svelte';
	import {
		CAPTURE_PATHS,
		HOST_KINDS,
		SOURCE_TYPES,
		addInput,
		addQueueItem,
		addStreamingTrack,
		capturePathLabel,
		connectWifi,
		deleteInput,
		deleteQueueItem,
		discoverDevices,
		discoverInputs,
		fmtMs,
		forgetWifiNetwork,
		getHealth,
		getStreamingStatus,
		getTransport,
		getWifiStatus,
		listDevices,
		listInputs,
		listQueue,
		listSources,
		listWifiNetworks,
		logoutSpotify,
		patchDevice,
		patchInput,
		patchQueueItem,
		putDevicesConfig,
		putTransport,
		saveWifiNetwork,
		searchStreaming,
		setActiveQueueItem,
		setActiveSource,
		sourceTypeLabel,
		uploadInputFile
	} from '$lib/api.js';

	/** @type {import('$lib/api.js').Device[]} */
	let devices = $state([]);
	/** @type {import('$lib/api.js').Source[]} */
	let sources = $state([]);
	/** @type {import('$lib/api.js').QueueItem[]} */
	let queue = $state([]);
	/** @type {import('$lib/api.js').InputHost[]} */
	let inputs = $state([]);
	/** @type {import('$lib/api.js').Transport | null} */
	let transport = $state(null);
	/** @type {import('$lib/api.js').Health | null} */
	let health = $state(null);
	/** @type {import('$lib/api.js').WifiStatus | null} */
	let wifi = $state(null);
	/** @type {import('$lib/api.js').WifiNetwork[]} */
	let wifiNetworks = $state([]);
	/** @type {string | null} */
	let selectedId = $state(null);
	let scrubbing = $state(false);
	let scrubValue = $state(0);
	let apiError = $state('');
	let wifiSsid = $state('');
	let wifiPassword = $state('');
	let wifiBusy = $state(false);
	let wifiMessage = $state('');
	let sourceError = $state('');
	let newHostName = $state('');
	/** @type {import('$lib/api.js').HostKind} */
	let newHostKind = $state('phone');
	/** @type {import('$lib/api.js').CapturePath} */
	let newHostPath = $state('bt_a2dp');

	/** @type {import('$lib/api.js').DiscoveredCaptureSource[]} */
	let discoveredCapture = $state([]);
	let captureDetail = $state('');
	let captureBusy = $state(false);
	let captureMessage = $state('');

	/** @type {import('$lib/api.js').DiscoveredBluetoothSink[]} */
	let discoveredBt = $state([]);
	/** @type {import('$lib/api.js').Device | null} */
	let sppDefault = $state(null);
	/** @type {Set<string>} */
	let setupBtNames = $state(new Set());
	let setupSppEnabled = $state(true);
	let setupSppHost = $state('0.0.0.0');
	let setupSppPort = $state(1234);
	let setupSppRate = $state(48000);
	let setupSppChannels = $state(1);
	let setupBusy = $state(false);
	let setupMessage = $state('');
	let setupDetail = $state('');
	let setupConfigOk = $state(true);

	/** @type {import('$lib/api.js').StreamingStatus | null} */
	let streaming = $state(null);
	/** @type {'spotify' | 'apple'} */
	let streamProvider = $state('spotify');
	let streamQuery = $state('');
	/** @type {import('$lib/api.js').StreamingSearchHit[]} */
	let streamHits = $state([]);
	let streamBusy = $state(false);
	let streamMessage = $state('');

	/** @type {Record<string, ReturnType<typeof setTimeout>>} */
	const patchTimers = {};

	/** Last server position sample — UI interpolates between 1s polls while playing. */
	let clockAnchorMs = 0;
	let clockAnchorAt = 0;

	const selected = $derived(devices.find((d) => d.id === selectedId) ?? null);
	const playing = $derived(transport?.state === 'playing');
	const wifiIpLabel = $derived(wifi?.ips?.length ? wifi.ips.join(', ') : '—');
	const activeQueueId = $derived(transport?.queue_item_id ?? null);
	const activeQueueItem = $derived(queue.find((q) => q.id === activeQueueId) ?? null);
	/** Play disabled only when the active cue is not ready (stubs / unbound live). */
	const activeIsStub = $derived(Boolean(activeQueueItem && !activeQueueItem.available));
	const liveHosts = $derived(inputs.filter((h) => h.connected && h.endpoint));

	/**
	 * @param {import('$lib/api.js').Transport} t
	 * @param {{ syncScrub?: boolean }} [opts]
	 */
	function applyTransport(t, opts = {}) {
		const syncScrub = opts.syncScrub !== false;
		transport = t;
		clockAnchorMs = t.position_ms || 0;
		clockAnchorAt = performance.now();
		if (syncScrub && !scrubbing) {
			scrubValue = displayedPositionMs();
		}
	}

	function displayedPositionMs() {
		const t = transport;
		if (!t) return 0;
		if (t.state !== 'playing') return t.position_ms || 0;
		const elapsed = performance.now() - clockAnchorAt;
		const next = clockAnchorMs + elapsed;
		const duration = t.duration_ms || 0;
		if (duration > 0) return Math.min(duration, next);
		return next;
	}
	const healthLabel = $derived.by(() => {
		if (apiError) return 'API offline';
		if (!health) return '…';
		const label = health.backend === 'mock' ? 'mock' : 'pipewire';
		return `${label} · ${health.enabled_count}/${health.sink_count} sinks · ${inputs.length}/4 hosts`;
	});

	const sourcesByType = $derived.by(() => {
		/** @type {Record<string, import('$lib/api.js').Source[]>} */
		const groups = { local: [], spotify: [], apple: [], input: [] };
		for (const s of sources) {
			const key = SOURCE_TYPES.includes(/** @type {*} */ (s.type)) ? s.type : 'local';
			(groups[key] ||= []).push(s);
		}
		return groups;
	});

	/** @param {string | null | undefined} id */
	function hostName(id) {
		if (!id) return '';
		return inputs.find((h) => h.id === id)?.name || id;
	}

	async function refreshWifi() {
		const [w, nets] = await Promise.all([getWifiStatus(), listWifiNetworks()]);
		wifi = w;
		wifiNetworks = nets;
	}

	async function onSaveWifi() {
		const ssid = wifiSsid.trim();
		if (!ssid || wifiBusy) return;
		wifiBusy = true;
		wifiMessage = '';
		try {
			const body = { ssid };
			if (wifiPassword) body.password = wifiPassword;
			await saveWifiNetwork(body);
			wifiPassword = '';
			wifiMessage = `Saved ${ssid}`;
			await refreshWifi();
		} catch (err) {
			wifiMessage = err instanceof Error ? err.message : String(err);
		} finally {
			wifiBusy = false;
		}
	}

	async function onJoinWifi(ssid = wifiSsid.trim()) {
		const target = (ssid || '').trim();
		if (!target || wifiBusy) return;
		wifiBusy = true;
		wifiMessage = '';
		try {
			const body = { ssid: target };
			if (wifiPassword && target === wifiSsid.trim()) body.password = wifiPassword;
			const status = await connectWifi(body);
			wifi = status;
			wifiPassword = '';
			const ips = status.ips?.length ? status.ips.join(', ') : '—';
			wifiMessage = status.connected
				? `Joined ${status.ssid || target} · IP ${ips}`
				: `Join requested for ${target}`;
			await refreshWifi();
		} catch (err) {
			wifiMessage = err instanceof Error ? err.message : String(err);
		} finally {
			wifiBusy = false;
		}
	}

	/** @param {string} ssid */
	async function onForgetWifi(ssid) {
		if (wifiBusy) return;
		wifiBusy = true;
		wifiMessage = '';
		try {
			await forgetWifiNetwork(ssid);
			wifiMessage = `Forgot ${ssid}`;
			await refreshWifi();
		} catch (err) {
			wifiMessage = err instanceof Error ? err.message : String(err);
		} finally {
			wifiBusy = false;
		}
	}

	async function refreshDiscovery() {
		const disc = await discoverDevices();
		discoveredBt = disc.bluetooth || [];
		sppDefault = disc.spp_default || null;
		setupDetail = disc.detail || '';
		setupConfigOk = disc.config_ok !== false;
		if (sppDefault) {
			setupSppEnabled = Boolean(sppDefault.enabled);
			setupSppHost = sppDefault.listen_host || '0.0.0.0';
			setupSppPort = sppDefault.listen_port || 1234;
			setupSppRate = sppDefault.sample_rate || 48000;
			setupSppChannels = sppDefault.channels || 1;
		}
		const names = new Set();
		for (const bt of discoveredBt) {
			if (bt.configured_id || devices.some((d) => d.kind !== 'network_spp' && d.name === bt.name && d.enabled)) {
				names.add(bt.name);
			}
		}
		// Also mark currently configured BT devices that match discovery names.
		for (const d of devices) {
			if (d.kind === 'network_spp') continue;
			if (discoveredBt.some((bt) => bt.name === d.name) && d.enabled) {
				names.add(d.name);
			}
		}
		setupBtNames = names;
	}

	async function refresh() {
		const [h, d, t, s, q, i, st] = await Promise.all([
			getHealth(),
			listDevices(),
			getTransport(),
			listSources(),
			listQueue(),
			listInputs(),
			getStreamingStatus().catch(() => null)
		]);
		health = h;
		devices = d;
		sources = s;
		queue = q;
		inputs = i;
		if (st) streaming = st;
		apiError = '';
		if (!selectedId && d.length) selectedId = d[0].id;
		applyTransport(t);
		await refreshWifi().catch(() => {});
		await refreshDiscovery().catch((err) => {
			setupMessage = err instanceof Error ? err.message : String(err);
		});
		await refreshCaptureDiscovery().catch(() => {});
	}

	async function refreshStreaming() {
		streaming = await getStreamingStatus();
	}

	async function onStreamSearch() {
		const q = streamQuery.trim();
		if (!q || streamBusy) return;
		streamBusy = true;
		streamMessage = '';
		try {
			const res = await searchStreaming(streamProvider, q);
			streamHits = res.hits || [];
			streamMessage = streamHits.length
				? `${streamHits.length} result${streamHits.length === 1 ? '' : 's'}`
				: 'No tracks found';
		} catch (err) {
			streamHits = [];
			streamMessage = err instanceof Error ? err.message : String(err);
		} finally {
			streamBusy = false;
		}
	}

	/** @param {import('$lib/api.js').StreamingSearchHit} hit */
	async function onQueueStreamHit(hit) {
		if (streamBusy) return;
		streamBusy = true;
		streamMessage = '';
		try {
			const item = await addStreamingTrack({
				provider: hit.provider,
				uri: hit.uri,
				title: hit.title,
				artist: hit.artist,
				duration_ms: hit.duration_ms ?? undefined,
				activate: true
			});
			queue = await listQueue();
			applyTransport(await getTransport());
			sources = await listSources();
			streamMessage = item.available
				? `Queued ${hit.title} — press Play`
				: item.note || `Queued ${hit.title} (not playable yet)`;
			sourceError = item.available ? '' : item.note || '';
		} catch (err) {
			streamMessage = err instanceof Error ? err.message : String(err);
		} finally {
			streamBusy = false;
		}
	}

	async function onSpotifyLogout() {
		streamBusy = true;
		try {
			streaming = await logoutSpotify();
			queue = await listQueue();
			sources = await listSources();
			streamMessage = 'Spotify disconnected';
		} catch (err) {
			streamMessage = err instanceof Error ? err.message : String(err);
		} finally {
			streamBusy = false;
		}
	}

	/** @param {string} name */
	function toggleSetupBt(name) {
		const next = new Set(setupBtNames);
		if (next.has(name)) next.delete(name);
		else next.add(name);
		setupBtNames = next;
	}

	async function saveDeviceSetup() {
		if (setupBusy) return;
		setupBusy = true;
		setupMessage = '';
		try {
			/** @type {import('$lib/api.js').DeviceConfigEntry[]} */
			const entries = [];
			for (const bt of discoveredBt) {
				if (!setupBtNames.has(bt.name)) continue;
				entries.push({
					id: bt.configured_id || undefined,
					name: bt.name,
					kind: 'bluetooth',
					enabled: true,
					pw_node: bt.pw_node || undefined
				});
			}
			entries.push({
				id: sppDefault?.id || 'spp-1',
				name: sppDefault?.name || 'Phone SPP',
				kind: 'network_spp',
				enabled: setupSppEnabled,
				listen_host: setupSppHost.trim() || '0.0.0.0',
				listen_port: Number(setupSppPort) || 1234,
				sample_rate: Number(setupSppRate) || 48000,
				channels: Number(setupSppChannels) || 1
			});
			const saved = await putDevicesConfig(entries);
			devices = saved;
			setupMessage = `Saved ${saved.length} sink${saved.length === 1 ? '' : 's'} — no YAML edit needed`;
			if (!selectedId && saved.length) selectedId = saved[0].id;
			await refreshDiscovery();
			health = await getHealth().catch(() => health);
		} catch (err) {
			setupMessage = err instanceof Error ? err.message : String(err);
		} finally {
			setupBusy = false;
		}
	}

	/**
	 * @param {Partial<Pick<import('$lib/api.js').Device, 'delay_ms' | 'volume' | 'mute' | 'enabled' | 'kind' | 'listen_host' | 'listen_port' | 'sample_rate' | 'channels'>>} partial
	 */
	function queueDevicePatch(partial) {
		const id = selectedId;
		if (!id) return;
		devices = devices.map((d) => (d.id === id ? { ...d, ...partial } : d));

		clearTimeout(patchTimers[id]);
		patchTimers[id] = setTimeout(async () => {
			try {
				const updated = await patchDevice(id, partial);
				devices = devices.map((d) => (d.id === id ? updated : d));
			} catch (err) {
				console.error(err);
				await refresh().catch(() => {});
			}
		}, 180);
	}

	/**
	 * @param {{ state?: import('$lib/api.js').TransportState, position_ms?: number }} body
	 */
	async function setTransport(body) {
		try {
			sourceError = '';
			applyTransport(await putTransport(body));
		} catch (err) {
			sourceError = err instanceof Error ? err.message : String(err);
		}
	}

	/** @param {string} id */
	async function chooseSource(id) {
		try {
			sourceError = '';
			const t = await setActiveSource(id);
			queue = await listQueue();
			applyTransport(t);
			const active = queue.find((q) => q.id === t.queue_item_id);
			if (active && !active.available) {
				sourceError =
					active.note ||
					`${sourceTypeLabel(active.source_type)} stub selected — switch a queue row to Local to play`;
			}
		} catch (err) {
			sourceError = err instanceof Error ? err.message : String(err);
		}
	}

	/** @param {string} id */
	async function chooseQueueItem(id) {
		try {
			sourceError = '';
			applyTransport(await setActiveQueueItem(id));
		} catch (err) {
			sourceError = err instanceof Error ? err.message : String(err);
		}
	}

	/**
	 * @param {string} id
	 * @param {import('$lib/api.js').SourceType} sourceType
	 */
	async function switchSongSource(id, sourceType) {
		try {
			sourceError = '';
			/** @type {{ source_type: import('$lib/api.js').SourceType, input_id?: string | null }} */
			const patch = { source_type: sourceType };
			if (sourceType === 'local') patch.input_id = null;
			else if (!queue.find((q) => q.id === id)?.input_id && inputs.length) {
				patch.input_id = inputs.find((h) => h.armed)?.id || inputs[0].id;
			}
			const updated = await patchQueueItem(id, patch);
			queue = queue.map((q) => (q.id === id ? updated : q));
			applyTransport(await getTransport());
			if (!updated.available) {
				sourceError = updated.note || `${sourceTypeLabel(sourceType)} is a stub — sinks stay linked`;
			}
		} catch (err) {
			sourceError = err instanceof Error ? err.message : String(err);
			await refresh().catch(() => {});
		}
	}

	/**
	 * @param {string} id
	 * @param {string} inputId
	 */
	async function switchSongHost(id, inputId) {
		try {
			sourceError = '';
			const updated = await patchQueueItem(id, { input_id: inputId || null });
			queue = queue.map((q) => (q.id === id ? updated : q));
			applyTransport(await getTransport());
			if (!updated.available) {
				sourceError = updated.note || 'Host-bound source is a stub — sinks stay linked';
			}
		} catch (err) {
			sourceError = err instanceof Error ? err.message : String(err);
		}
	}

	/** @param {import('$lib/api.js').SourceType} sourceType */
	async function addStubSong(sourceType) {
		try {
			sourceError = '';
			const hostId =
				sourceType === 'local'
					? undefined
					: liveHosts[0]?.id || inputs.find((h) => h.armed)?.id || inputs[0]?.id;
			const item = await addQueueItem({
				source_type: sourceType,
				input_id: hostId,
				title:
					sourceType === 'spotify'
						? 'Spotify from phone/PC (stub)'
						: sourceType === 'apple'
							? 'Apple Music from phone/PC (stub)'
							: liveHosts.length
								? `Live · ${hostName(hostId || '')}`
								: 'Live input cue'
			});
			queue = [...queue, item];
			if (!item.available) {
				sourceError = item.note || `${sourceTypeLabel(sourceType)} stub — bind a live host`;
			} else {
				sourceError = '';
			}
		} catch (err) {
			sourceError = err instanceof Error ? err.message : String(err);
		}
	}

	async function refreshCaptureDiscovery() {
		captureBusy = true;
		captureMessage = '';
		try {
			const res = await discoverInputs();
			discoveredCapture = res.sources || [];
			captureDetail = res.detail || '';
			inputs = await listInputs();
			sources = await listSources();
			queue = await listQueue();
			captureMessage = discoveredCapture.length
				? `${discoveredCapture.length} live source${discoveredCapture.length === 1 ? '' : 's'}`
				: 'No bluez_input / USB sources yet — pair phone as A2DP source';
		} catch (err) {
			captureMessage = err instanceof Error ? err.message : String(err);
		} finally {
			captureBusy = false;
		}
	}

	/**
	 * Bind a discovered PipeWire source onto a registered host (or create one).
	 * @param {import('$lib/api.js').DiscoveredCaptureSource} src
	 */
	async function bindCaptureSource(src) {
		try {
			sourceError = '';
			const endpoint = src.pw_name || src.pw_node || '';
			const path = /** @type {import('$lib/api.js').CapturePath} */ (
				src.capture_path || 'bt_a2dp'
			);
			let host = inputs.find((h) => h.id === src.configured_id) ||
				inputs.find((h) => h.name === src.name) ||
				inputs.find((h) => h.capture_path === path && !h.connected);
			if (host) {
				host = await patchInput(host.id, {
					name: src.name,
					capture_path: path,
					endpoint,
					connected: true,
					armed: true
				});
				inputs = inputs.map((h) => (h.id === host.id ? host : h));
			} else if (inputs.length < 4) {
				host = await addInput({
					name: src.name,
					kind: 'phone',
					capture_path: path,
					endpoint,
					connected: true,
					armed: true
				});
				inputs = [...inputs, host];
			} else {
				throw new Error('Already at 4 hosts — remove one before binding');
			}
			sources = await listSources();
			queue = await listQueue();
			captureMessage = `Bound ${src.name} → ${endpoint}`;
		} catch (err) {
			sourceError = err instanceof Error ? err.message : String(err);
		}
	}

	/**
	 * @param {string} hostId
	 * @param {Event} e
	 */
	async function onHostUpload(hostId, e) {
		const input = /** @type {HTMLInputElement} */ (e.currentTarget);
		const file = input.files?.[0];
		if (!file) return;
		try {
			sourceError = '';
			const updated = await uploadInputFile(hostId, file);
			inputs = inputs.map((h) => (h.id === hostId ? updated : h));
			sources = await listSources();
			queue = await listQueue();
			captureMessage = `Uploaded ${file.name} → feeder for ${updated.name}`;
		} catch (err) {
			sourceError = err instanceof Error ? err.message : String(err);
		} finally {
			input.value = '';
		}
	}

	async function registerHost() {
		try {
			sourceError = '';
			const host = await addInput({
				name: newHostName.trim() || 'Phone / PC',
				kind: newHostKind,
				capture_path: newHostPath,
				armed: true
			});
			inputs = [...inputs, host];
			sources = await listSources();
			newHostName = '';
			await refreshCaptureDiscovery().catch(() => {});
		} catch (err) {
			sourceError = err instanceof Error ? err.message : String(err);
		}
	}

	/**
	 * @param {string} id
	 * @param {Partial<import('$lib/api.js').InputHost>} patch
	 */
	async function updateHost(id, patch) {
		try {
			const updated = await patchInput(id, patch);
			inputs = inputs.map((h) => (h.id === id ? updated : h));
			sources = await listSources();
			queue = await listQueue();
		} catch (err) {
			sourceError = err instanceof Error ? err.message : String(err);
		}
	}

	/** @param {string} id */
	async function removeHost(id) {
		try {
			await deleteInput(id);
			inputs = inputs.filter((h) => h.id !== id);
			sources = await listSources();
			queue = await listQueue();
			applyTransport(await getTransport());
		} catch (err) {
			sourceError = err instanceof Error ? err.message : String(err);
		}
	}

	/** @param {string} id */
	async function removeSong(id) {
		try {
			sourceError = '';
			await deleteQueueItem(id);
			queue = queue.filter((q) => q.id !== id);
			applyTransport(await getTransport());
		} catch (err) {
			sourceError = err instanceof Error ? err.message : String(err);
		}
	}

	onMount(() => {
		let cancelled = false;
		/** @type {ReturnType<typeof setInterval> | undefined} */
		let pollTimer;
		/** @type {ReturnType<typeof setInterval> | undefined} */
		let tickTimer;

		refresh()
			.then(() => {
				if (cancelled) return;
				const params = new URLSearchParams(window.location.search);
				if (params.get('spotify') === 'connected') {
					streamMessage = 'Spotify connected';
					refreshStreaming().catch(() => {});
					history.replaceState({}, '', window.location.pathname);
				} else if (params.get('spotify') === 'error') {
					streamMessage = `Spotify auth failed: ${params.get('detail') || 'error'}`;
					history.replaceState({}, '', window.location.pathname);
				}
				// Server clock (~1 Hz); UI interpolates between samples while playing.
				pollTimer = setInterval(async () => {
					try {
						applyTransport(await getTransport());
					} catch (err) {
						console.error(err);
					}
				}, 1000);
				tickTimer = setInterval(() => {
					if (!scrubbing && transport?.state === 'playing') {
						scrubValue = displayedPositionMs();
					}
				}, 200);
			})
			.catch((err) => {
				apiError = err instanceof Error ? err.message : String(err);
				console.error(err);
			});

		return () => {
			cancelled = true;
			clearInterval(pollTimer);
			clearInterval(tickTimer);
			for (const t of Object.values(patchTimers)) clearTimeout(t);
		};
	});
</script>

<div class="atmosphere" aria-hidden="true"></div>

<header class="top">
	<div class="brand-block">
		<p class="brand">MuSync</p>
		<p class="tagline">Multi-room Bluetooth, one clock</p>
	</div>
	<div
		class="health"
		data-ok={apiError ? 'false' : health ? String(Boolean(health.ok)) : undefined}
		title={health?.detail || apiError || ''}
	>
		{healthLabel}
	</div>
</header>

<main class="layout">
	<section class="panel transport-panel" aria-labelledby="transport-heading">
		<div class="panel-head">
			<h2 id="transport-heading">Transport</h2>
			<p class="now-playing">
				{transport?.title || 'No track'}
				{#if transport?.source_type}
					<span class="type-chip">{sourceTypeLabel(transport.source_type)}</span>
				{/if}
			</p>
		</div>
				{#if activeIsStub}
			<p class="stub-banner" role="status">
				Active song is not playable yet
				{#if transport?.source_type === 'input'}
					— bind a live bluez_input / USB / upload on a Phone/PC host, then Play.
				{:else if transport?.source_type === 'spotify' || transport?.source_type === 'apple'}
					— connect/search in Streaming, or switch the song to Local.
				{:else}
					— sink graph stays up; switch the song to Local or a live input.
				{/if}
			</p>
		{/if}
		{#if sourceError}
			<p class="stub-banner warn" role="alert">{sourceError}</p>
		{/if}
		<div class="transport-controls">
			<button
				type="button"
				class="btn"
				aria-label="Play"
				disabled={playing || activeIsStub}
				onclick={() => setTransport({ state: 'playing' })}
			>
				Play
			</button>
			<button
				type="button"
				class="btn"
				aria-label="Pause"
				disabled={!playing}
				onclick={() => setTransport({ state: 'paused' })}
			>
				Pause
			</button>
			<button type="button" class="btn btn-ghost" aria-label="Stop" onclick={() => setTransport({ state: 'stopped' })}>
				Stop
			</button>
		</div>
		<div class="scrub">
			<span>{fmtMs(scrubValue)}</span>
			<input
				type="range"
				min="0"
				max={transport?.duration_ms || 0}
				step="100"
				value={scrubValue}
				aria-label="Playback position"
				disabled={activeIsStub}
				onpointerdown={() => {
					scrubbing = true;
				}}
				onpointerup={async (e) => {
					scrubbing = false;
					const next = Number(/** @type {HTMLInputElement} */ (e.currentTarget).value);
					scrubValue = next;
					await setTransport({ position_ms: next });
				}}
				onchange={async (e) => {
					scrubbing = false;
					const next = Number(/** @type {HTMLInputElement} */ (e.currentTarget).value);
					scrubValue = next;
					await setTransport({ position_ms: next });
				}}
				oninput={(e) => {
					scrubValue = Number(/** @type {HTMLInputElement} */ (e.currentTarget).value);
				}}
			/>
			<span>{fmtMs(transport?.duration_ms || 0)}</span>
		</div>
	</section>

	<section class="panel setup-panel" aria-labelledby="setup-heading">
		<div class="panel-head">
			<h2 id="setup-heading">Device setup</h2>
			<p class="hint">Pick BT by name · set SPP type details · save — no YAML / MAC typing</p>
		</div>
		{#if !setupConfigOk}
			<p class="setup-warn">Config missing or broken — started from defaults. Save below to write devices.yaml.</p>
		{/if}
		<div class="setup-grid">
			<div class="setup-col">
				<h3 class="setup-sub">Bluetooth (auto-discovered)</h3>
				{#if discoveredBt.length === 0}
					<p class="hint">No bluez sinks yet — pair a speaker, then Refresh.</p>
				{:else}
					<ul class="setup-list">
						{#each discoveredBt as bt (bt.name + (bt.mac || ''))}
							<li>
								<label class="check setup-check">
									<input
										type="checkbox"
										checked={setupBtNames.has(bt.name)}
										onchange={() => toggleSetupBt(bt.name)}
									/>
									<span>
										<strong>{bt.name}</strong>
										<span class="device-sub">
											{bt.connected ? 'connected' : bt.paired ? 'paired' : 'seen'}
											{#if bt.pw_name}
												· {bt.pw_name}
											{/if}
										</span>
									</span>
								</label>
							</li>
						{/each}
					</ul>
				{/if}
			</div>
			<div class="setup-col">
				<h3 class="setup-sub">Stream / sink type</h3>
				<label class="check setup-check">
					<input type="checkbox" bind:checked={setupSppEnabled} />
					<span>
						<strong>Phone SPP</strong>
						<span class="device-sub">network_spp · TCP listen for Simple Protocol Player</span>
					</span>
				</label>
				{#if setupSppEnabled}
					<div class="setup-spp-fields">
						<label class="field">
							<span>Listen host</span>
							<input type="text" bind:value={setupSppHost} autocomplete="off" />
						</label>
						<label class="field">
							<span>Port</span>
							<input type="number" min="1" max="65535" bind:value={setupSppPort} />
						</label>
						<label class="field">
							<span>Sample rate</span>
							<select bind:value={setupSppRate}>
								<option value={44100}>44100</option>
								<option value={48000}>48000</option>
							</select>
						</label>
						<label class="field">
							<span>Channels</span>
							<select bind:value={setupSppChannels}>
								<option value={1}>Mono (1)</option>
								<option value={2}>Stereo (2)</option>
							</select>
						</label>
					</div>
				{/if}
			</div>
		</div>
		<div class="setup-actions">
			<button type="button" class="btn btn-ghost" disabled={setupBusy} onclick={() => refreshDiscovery()}>
				Refresh discovery
			</button>
			<button type="button" class="btn" disabled={setupBusy} onclick={() => saveDeviceSetup()}>
				{setupBusy ? 'Saving…' : 'Save sinks'}
			</button>
		</div>
		{#if setupMessage}
			<p class="setup-msg">{setupMessage}</p>
		{/if}
		{#if setupDetail}
			<p class="hint setup-detail">{setupDetail}</p>
		{/if}
	</section>

	<div class="split">
		<section class="panel devices-panel" aria-labelledby="devices-heading">
			<div class="panel-head">
				<h2 id="devices-heading">Devices</h2>
				<p class="hint">Configured sinks · tap a row for delay &amp; volume</p>
			</div>
			<ul class="device-list">
				{#each devices as d (d.id)}
					<li>
						<button
							type="button"
							class="device-row"
							class:selected={d.id === selectedId}
							aria-pressed={d.id === selectedId}
							onclick={() => {
								selectedId = d.id;
							}}
						>
							<span>
								<span class="device-title">
									{d.name}
									{#if d.kind === 'network_spp'}
										<span class="type-chip">SPP</span>
									{:else}
										<span class="type-chip">BT</span>
									{/if}
								</span>
								<span class="device-sub">
									{#if d.kind === 'network_spp'}
										tcp://{d.listen_host || '0.0.0.0'}:{d.listen_port || 1234} · {d.sample_rate || 48000} Hz · {d.channels || 1} ch · delay {d.delay_ms} ms · vol {Math.round(d.volume * 100)}%
									{:else}
										{d.name} · delay {d.delay_ms} ms · vol {Math.round(d.volume * 100)}%
									{/if}
								</span>
							</span>
							<span class="status-pill {d.connected ? 'linked' : 'missing'}">
								{d.kind === 'network_spp'
									? d.enabled
										? 'listen'
										: 'off'
									: d.connected
										? 'linked'
										: 'missing'}{d.enabled || d.kind === 'network_spp' ? '' : ' · off'}
							</span>
						</button>
					</li>
				{/each}
			</ul>
		</section>

		{#if selected}
			<section class="panel detail-panel" aria-labelledby="detail-heading">
				<div class="panel-head">
					<h2 id="detail-heading">Sink controls</h2>
					<button
						type="button"
						class="btn btn-ghost btn-small"
						onclick={() => {
							selectedId = null;
						}}
					>
						Close
					</button>
				</div>
				<p class="detail-name">{selected.name}</p>
				<p class="detail-meta">
					{#if selected.kind === 'network_spp'}
						SPP TCP · {selected.listen_host}:{selected.listen_port} · {selected.sample_rate} Hz / {selected.channels} ch · {selected.connected ? 'listening' : 'idle'}
					{:else}
						Bluetooth · {selected.connected ? 'linked' : 'waiting for bluez'} · use Device setup to pick by name
					{/if}
				</p>

				<label class="field">
					<span>Sink type</span>
					<select
						value={selected.kind || 'bluetooth'}
						onchange={(e) => {
							queueDevicePatch({
								kind: /** @type {import('$lib/api.js').DeviceKind} */ (
									/** @type {HTMLSelectElement} */ (e.currentTarget).value
								)
							});
						}}
					>
						<option value="bluetooth">Bluetooth (A2DP)</option>
						<option value="network_spp">Phone SPP (TCP)</option>
					</select>
				</label>

				{#if selected.kind === 'network_spp'}
					<label class="field">
						<span>Listen host</span>
						<input
							type="text"
							value={selected.listen_host || '0.0.0.0'}
							oninput={(e) => {
								queueDevicePatch({
									listen_host: /** @type {HTMLInputElement} */ (e.currentTarget).value
								});
							}}
						/>
					</label>
					<label class="field">
						<span>Listen port</span>
						<input
							type="number"
							min="1"
							max="65535"
							value={selected.listen_port || 1234}
							oninput={(e) => {
								queueDevicePatch({
									listen_port: Number(/** @type {HTMLInputElement} */ (e.currentTarget).value)
								});
							}}
						/>
					</label>
					<label class="field">
						<span>Sample rate</span>
						<select
							value={selected.sample_rate || 48000}
							onchange={(e) => {
								queueDevicePatch({
									sample_rate: Number(/** @type {HTMLSelectElement} */ (e.currentTarget).value)
								});
							}}
						>
							<option value="44100">44100</option>
							<option value="48000">48000</option>
						</select>
					</label>
					<label class="field">
						<span>Channels</span>
						<select
							value={selected.channels || 1}
							onchange={(e) => {
								queueDevicePatch({
									channels: Number(/** @type {HTMLSelectElement} */ (e.currentTarget).value)
								});
							}}
						>
							<option value="1">Mono (1)</option>
							<option value="2">Stereo (2)</option>
						</select>
					</label>
				{/if}

				<label class="field">
					<span>Delay <em>{selected.delay_ms} ms</em></span>
					<input
						type="range"
						min="0"
						max="500"
						step="5"
						value={selected.delay_ms}
						oninput={(e) => {
							queueDevicePatch({ delay_ms: Number(/** @type {HTMLInputElement} */ (e.currentTarget).value) });
						}}
					/>
				</label>
				<label class="field">
					<span>Volume <em>{Math.round(selected.volume * 100)}%</em></span>
					<input
						type="range"
						min="0"
						max="100"
						step="1"
						value={Math.round(selected.volume * 100)}
						oninput={(e) => {
							const pct = Number(/** @type {HTMLInputElement} */ (e.currentTarget).value);
							queueDevicePatch({ volume: pct / 100 });
						}}
					/>
				</label>
				<div class="toggles">
					<label class="check">
						<input
							type="checkbox"
							checked={selected.mute}
							onchange={(e) => {
								queueDevicePatch({ mute: /** @type {HTMLInputElement} */ (e.currentTarget).checked });
							}}
						/>
						<span>Mute</span>
					</label>
					<label class="check">
						<input
							type="checkbox"
							checked={selected.enabled}
							onchange={(e) => {
								queueDevicePatch({ enabled: /** @type {HTMLInputElement} */ (e.currentTarget).checked });
							}}
						/>
						<span>Enabled in session</span>
					</label>
				</div>
			</section>
		{/if}
	</div>

	<section class="panel inputs-panel" aria-labelledby="inputs-heading">
		<div class="panel-head">
			<h2 id="inputs-heading">Phone / PC hosts</h2>
			<p class="hint">
				Up to 4 · BT A2DP source (bluez_input) → feeder · {liveHosts.length} live
			</p>
		</div>
		<div class="stub-actions">
			<button
				type="button"
				class="btn btn-ghost btn-small"
				disabled={captureBusy}
				onclick={() => refreshCaptureDiscovery()}
			>
				{captureBusy ? 'Scanning…' : 'Refresh live inputs'}
			</button>
			{#if captureMessage}
				<span class="hint">{captureMessage}</span>
			{/if}
		</div>
		{#if discoveredCapture.length}
			<ul class="queue-list discovery-list">
				{#each discoveredCapture as src (src.pw_name || src.name)}
					<li class="queue-row host-row">
						<div class="queue-main">
							<span class="source-title">{src.name}</span>
							<span class="source-sub">
								{capturePathLabel(src.capture_path || 'bt_a2dp')} · {src.pw_name || src.pw_node}
								{#if src.mac}
									· {src.mac}
								{/if}
							</span>
						</div>
						<button
							type="button"
							class="btn btn-small"
							onclick={() => bindCaptureSource(src)}
						>
							{src.configured_id ? 'Re-bind' : 'Use as input'}
						</button>
					</li>
				{/each}
			</ul>
		{/if}
		<form
			class="host-form"
			onsubmit={(e) => {
				e.preventDefault();
				registerHost();
			}}
		>
			<input
				type="text"
				placeholder="Name (e.g. S21 FE)"
				bind:value={newHostName}
				aria-label="Host name"
				maxlength="64"
			/>
			<select bind:value={newHostKind} aria-label="Host kind">
				{#each HOST_KINDS as k}
					<option value={k}>{k}</option>
				{/each}
			</select>
			<select bind:value={newHostPath} aria-label="Capture path">
				{#each CAPTURE_PATHS as p}
					<option value={p}>{capturePathLabel(p)}</option>
				{/each}
			</select>
			<button type="submit" class="btn btn-small" disabled={inputs.length >= 4}>Register</button>
		</form>
		<ul class="queue-list">
			{#each inputs as host (host.id)}
				<li class="queue-row host-row">
					<div class="queue-main">
						<span class="source-title">{host.name}</span>
						<span class="source-sub">
							{host.kind} · {capturePathLabel(host.capture_path)}
							{#if host.endpoint}
								· {host.endpoint}
							{/if}
							{#if host.note}
								· {host.note}
							{/if}
						</span>
					</div>
					<label class="type-select">
						<span class="sr-only">Capture path</span>
						<select
							value={host.capture_path}
							aria-label={`Capture path for ${host.name}`}
							onchange={(e) => {
								const next = /** @type {HTMLSelectElement} */ (e.currentTarget).value;
								updateHost(host.id, {
									capture_path: /** @type {import('$lib/api.js').CapturePath} */ (next)
								});
							}}
						>
							{#each CAPTURE_PATHS as p}
								<option value={p}>{capturePathLabel(p)}</option>
							{/each}
						</select>
					</label>
					<label class="check">
						<input
							type="checkbox"
							checked={host.armed}
							onchange={(e) => {
								updateHost(host.id, {
									armed: /** @type {HTMLInputElement} */ (e.currentTarget).checked
								});
							}}
						/>
						<span>Armed</span>
					</label>
					<span class="status-pill {host.connected ? 'linked' : 'missing'}">
						{host.connected ? 'live' : 'waiting'}
					</span>
					{#if host.capture_path === 'network'}
						<label class="btn btn-ghost btn-small upload-btn">
							Upload
							<input
								type="file"
								accept="audio/*,.wav,.flac,.mp3,.ogg"
								class="sr-only"
								onchange={(e) => onHostUpload(host.id, e)}
							/>
						</label>
					{/if}
					<button
						type="button"
						class="btn btn-ghost btn-small"
						aria-label={`Remove ${host.name}`}
						onclick={() => removeHost(host.id)}
					>
						Remove
					</button>
				</li>
			{:else}
				<li class="empty-group">No phone/PC hosts yet — register one or bind a discovered source</li>
			{/each}
		</ul>
		{#if captureDetail}
			<p class="hint muted">{captureDetail}</p>
		{/if}
	</section>

	<section class="panel streaming-panel" aria-labelledby="streaming-heading">
		<div class="panel-head">
			<h2 id="streaming-heading">Streaming</h2>
			<p class="hint">Spotify → musync_feeder · Apple limited on Linux</p>
		</div>
		<div class="stream-status">
			<div class="stream-card">
				<strong>Spotify</strong>
				<span class="status-pill {streaming?.spotify?.authenticated ? 'linked' : 'missing'}">
					{streaming?.spotify?.authenticated
						? streaming.spotify.display_name || 'connected'
						: streaming?.spotify?.configured
							? 'not connected'
							: 'not configured'}
				</span>
				{#if streaming?.spotify?.authenticated}
					<button
						type="button"
						class="btn btn-ghost btn-small"
						disabled={streamBusy}
						onclick={() => onSpotifyLogout()}
					>
						Disconnect
					</button>
				{:else if streaming?.spotify?.configured}
					<a class="btn btn-small" href="/api/streaming/spotify/login">Connect Spotify</a>
				{:else}
					<span class="muted tiny">Set MUSYNC_SPOTIFY_CLIENT_ID/SECRET — docs/spotify-apple.md</span>
				{/if}
				{#if streaming?.spotify?.note}
					<p class="hint muted">{streaming.spotify.note}</p>
				{/if}
			</div>
			<div class="stream-card">
				<strong>Apple Music</strong>
				<span class="status-pill {streaming?.apple?.authenticated ? 'linked' : 'missing'}">
					{streaming?.apple?.authenticated
						? streaming.apple.playback_mode === 'airplay_capture'
							? 'search + AirPlay'
							: 'search only'
						: 'not configured'}
				</span>
				{#if streaming?.apple?.note}
					<p class="hint muted">{streaming.apple.note}</p>
				{/if}
			</div>
		</div>
		<form
			class="stream-search"
			onsubmit={(e) => {
				e.preventDefault();
				onStreamSearch();
			}}
		>
			<label class="type-select">
				<span class="sr-only">Provider</span>
				<select
					bind:value={streamProvider}
					aria-label="Streaming provider"
				>
					<option value="spotify">Spotify</option>
					<option value="apple">Apple Music</option>
				</select>
			</label>
			<input
				type="search"
				placeholder="Search tracks"
				bind:value={streamQuery}
				aria-label="Search tracks"
			/>
			<button type="submit" class="btn btn-small" disabled={streamBusy || !streamQuery.trim()}>
				Search
			</button>
		</form>
		{#if streamMessage}
			<p class="hint" role="status">{streamMessage}</p>
		{/if}
		<ul class="queue-list stream-hits">
			{#each streamHits as hit (hit.uri)}
				<li class="queue-row">
					<div class="queue-main">
						<span class="source-title">{hit.title}</span>
						<span class="source-sub"
							>{hit.artist}{#if hit.album}
								· {hit.album}{/if}</span
						>
					</div>
					<button
						type="button"
						class="btn btn-small"
						disabled={streamBusy}
						onclick={() => onQueueStreamHit(hit)}
					>
						Queue &amp; play
					</button>
				</li>
			{:else}
				<li class="empty-group">Search to pick a track</li>
			{/each}
		</ul>
	</section>

	<section class="panel queue-panel" aria-labelledby="queue-heading">
		<div class="panel-head">
			<h2 id="queue-heading">Song queue</h2>
			<p class="hint">Per-song type + host · switch without tearing down sinks</p>
		</div>
		<div class="stub-actions">
			<button type="button" class="btn btn-ghost btn-small" onclick={() => addStubSong('spotify')}>
				+ Host Spotify
			</button>
			<button type="button" class="btn btn-ghost btn-small" onclick={() => addStubSong('apple')}>
				+ Host Apple
			</button>
			<button type="button" class="btn btn-ghost btn-small" onclick={() => addStubSong('input')}>
				+ Live input{liveHosts.length ? ` (${liveHosts[0].name})` : ''}
			</button>
		</div>
		<ul class="queue-list">
			{#each queue as item (item.id)}
				<li class="queue-row queue-row-host" class:active={item.id === activeQueueId}>
					<button type="button" class="queue-main" onclick={() => chooseQueueItem(item.id)}>
						<span class="source-title">{item.title}</span>
						<span class="source-sub">
							{item.locator || item.source_id || item.id}
							{#if item.input_id}
								· {hostName(item.input_id)}
							{/if}
							{#if item.id === activeQueueId}
								· active
							{/if}
							{#if item.note}
								· {item.note}
							{/if}
						</span>
					</button>
					<label class="type-select">
						<span class="sr-only">Source type</span>
						<select
							value={item.source_type}
							aria-label={`Source type for ${item.title}`}
							onchange={(e) => {
								const next = /** @type {HTMLSelectElement} */ (e.currentTarget).value;
								switchSongSource(item.id, /** @type {import('$lib/api.js').SourceType} */ (next));
							}}
						>
							{#each SOURCE_TYPES as t}
								<option value={t}
									>{sourceTypeLabel(t)}{t === 'local' ||
									(t === 'input' && liveHosts.length) ||
									(t === 'spotify' && streaming?.spotify?.authenticated) ||
									(t === 'apple' && streaming?.apple?.authenticated)
										? ''
										: ' (stub)'}</option
								>
							{/each}
						</select>
					</label>
					<label class="type-select">
						<span class="sr-only">Phone or PC host</span>
						<select
							value={item.input_id || ''}
							aria-label={`Host for ${item.title}`}
							onchange={(e) => {
								const next = /** @type {HTMLSelectElement} */ (e.currentTarget).value;
								switchSongHost(item.id, next);
							}}
						>
							<option value="">Hub (no host)</option>
							{#each inputs as h}
								<option value={h.id}>{h.name}</option>
							{/each}
						</select>
					</label>
					<span class="status-pill {item.available ? 'linked' : 'missing'}">
						{item.available ? 'ready' : 'stub'}
					</span>
					<button
						type="button"
						class="btn btn-ghost btn-small"
						aria-label={`Remove ${item.title}`}
						onclick={() => removeSong(item.id)}
					>
						Remove
					</button>
				</li>
			{/each}
		</ul>
	</section>

	<section class="panel sources-panel" aria-labelledby="sources-heading">
		<div class="panel-head">
			<h2 id="sources-heading">Source catalog</h2>
			<p class="hint">Hub local files · live BT/USB/upload when linked · Spotify/Apple stubs</p>
		</div>
		{#each SOURCE_TYPES as type}
			<div class="source-group">
				<h3>{sourceTypeLabel(type)}</h3>
				<ul class="source-list">
					{#each sourcesByType[type] || [] as s (s.id)}
						<li>
							<button
								type="button"
								class="source-row"
								class:active-source={s.id === transport?.source_id}
								class:stub-source={!s.available}
								title={s.available ? 'Playable hub local' : s.note || 'Stub — sink graph stays up'}
								onclick={() => chooseSource(s.id)}
							>
								<span>
									<span class="source-title">{s.title}</span>
									<span class="source-sub">
										{s.type}{s.id === transport?.source_id ? ' · active' : ''}{s.input_id
											? ' · host stub'
											: ''}{s.note ? ` · ${s.note}` : ''}
									</span>
								</span>
								<span class="status-pill {s.available ? 'linked' : 'missing'}">
									{s.available ? 'ready' : 'stub'}
								</span>
							</button>
						</li>
					{:else}
						<li class="empty-group">No {sourceTypeLabel(type).toLowerCase()} entries</li>
					{/each}
				</ul>
			</div>
		{/each}
	</section>

	<section class="panel wifi-panel" aria-labelledby="wifi-heading">
		<div class="panel-head">
			<h2 id="wifi-heading">Wi-Fi</h2>
			<p class="hint">Join a phone hotspot without SSH · IP for SPP</p>
		</div>

		<div class="wifi-status" data-ok={wifi ? String(Boolean(wifi.connected)) : undefined}>
			<p class="wifi-line">
				<span class="wifi-label">Now</span>
				{#if wifi?.connected}
					{wifi.ssid || 'associated'} · {wifi.interface || 'wlan?'} · {wifi.backend}
				{:else}
					not associated · {wifi?.backend || '…'}
				{/if}
			</p>
			<p class="wifi-line">
				<span class="wifi-label">IP</span>
				<code class="wifi-ip">{wifiIpLabel}</code>
			</p>
			{#if wifi?.detail}
				<p class="wifi-detail">{wifi.detail}</p>
			{/if}
		</div>

		<form
			class="wifi-form"
			onsubmit={(e) => {
				e.preventDefault();
				onJoinWifi();
			}}
		>
			<label class="field">
				<span>SSID</span>
				<input
					type="text"
					name="ssid"
					autocomplete="off"
					maxlength="32"
					placeholder="Phone hotspot name"
					bind:value={wifiSsid}
					disabled={wifiBusy}
				/>
			</label>
			<label class="field">
				<span>Password</span>
				<input
					type="password"
					name="password"
					autocomplete="new-password"
					maxlength="63"
					placeholder="Leave blank if already saved"
					bind:value={wifiPassword}
					disabled={wifiBusy}
				/>
			</label>
			<div class="wifi-actions">
				<button type="submit" class="btn" disabled={wifiBusy || !wifiSsid.trim()}>Join</button>
				<button
					type="button"
					class="btn btn-ghost"
					disabled={wifiBusy || !wifiSsid.trim()}
					onclick={() => onSaveWifi()}
				>
					Save only
				</button>
			</div>
		</form>

		{#if wifiMessage}
			<p class="wifi-message" role="status">{wifiMessage}</p>
		{/if}

		<ul class="wifi-list">
			{#each wifiNetworks as n (n.ssid)}
				<li class="wifi-row">
					<span>
						<span class="device-title">{n.ssid}</span>
						<span class="device-sub">
							{n.active ? 'active' : 'saved'}{n.has_password ? ' · key stored' : ' · open / no key'}
						</span>
					</span>
					<span class="wifi-row-actions">
						<button
							type="button"
							class="btn btn-small"
							disabled={wifiBusy || n.active}
							onclick={() => onJoinWifi(n.ssid)}
						>
							Join
						</button>
						<button
							type="button"
							class="btn btn-ghost btn-small"
							disabled={wifiBusy}
							onclick={() => onForgetWifi(n.ssid)}
						>
							Forget
						</button>
					</span>
				</li>
			{:else}
				<li class="wifi-empty">No saved networks yet</li>
			{/each}
		</ul>
	</section>
</main>
