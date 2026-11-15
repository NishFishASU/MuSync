<script>
	import { onMount } from 'svelte';
	import {
		SOURCE_TYPES,
		addQueueItem,
		deleteQueueItem,
		fmtMs,
		getHealth,
		getTransport,
		listDevices,
		listQueue,
		listSources,
		patchDevice,
		patchQueueItem,
		putTransport,
		setActiveQueueItem,
		setActiveSource,
		sourceTypeLabel
	} from '$lib/api.js';

	/** @type {import('$lib/api.js').Device[]} */
	let devices = $state([]);
	/** @type {import('$lib/api.js').Source[]} */
	let sources = $state([]);
	/** @type {import('$lib/api.js').QueueItem[]} */
	let queue = $state([]);
	/** @type {import('$lib/api.js').Transport | null} */
	let transport = $state(null);
	/** @type {import('$lib/api.js').Health | null} */
	let health = $state(null);
	/** @type {string | null} */
	let selectedId = $state(null);
	let scrubbing = $state(false);
	let scrubValue = $state(0);
	let apiError = $state('');
	let sourceError = $state('');

	/** @type {Record<string, ReturnType<typeof setTimeout>>} */
	const patchTimers = {};

	const selected = $derived(devices.find((d) => d.id === selectedId) ?? null);
	const playing = $derived(transport?.state === 'playing');
	const activeQueueId = $derived(transport?.queue_item_id ?? null);
	const activeIsStub = $derived(
		Boolean(transport?.source_type && transport.source_type !== 'local')
	);
	const healthLabel = $derived.by(() => {
		if (apiError) return 'API offline';
		if (!health) return '…';
		const label = health.backend === 'mock' ? 'mock' : 'pipewire';
		return `${label} · ${health.enabled_count}/${health.sink_count} sinks`;
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

	async function refresh() {
		const [h, d, t, s, q] = await Promise.all([
			getHealth(),
			listDevices(),
			getTransport(),
			listSources(),
			listQueue()
		]);
		health = h;
		devices = d;
		transport = t;
		sources = s;
		queue = q;
		apiError = '';
		if (!selectedId && d.length) selectedId = d[0].id;
		if (!scrubbing) scrubValue = t.position_ms || 0;
	}

	/**
	 * @param {Partial<Pick<import('$lib/api.js').Device, 'delay_ms' | 'volume' | 'mute' | 'enabled'>>} partial
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
			transport = await putTransport(body);
			if (!scrubbing) scrubValue = transport.position_ms || 0;
		} catch (err) {
			sourceError = err instanceof Error ? err.message : String(err);
		}
	}

	/** @param {string} id */
	async function chooseSource(id) {
		try {
			sourceError = '';
			transport = await setActiveSource(id);
			queue = await listQueue();
			if (!scrubbing) scrubValue = transport.position_ms || 0;
		} catch (err) {
			sourceError = err instanceof Error ? err.message : String(err);
		}
	}

	/** @param {string} id */
	async function chooseQueueItem(id) {
		try {
			sourceError = '';
			transport = await setActiveQueueItem(id);
			if (!scrubbing) scrubValue = transport.position_ms || 0;
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
			const updated = await patchQueueItem(id, { source_type: sourceType });
			queue = queue.map((q) => (q.id === id ? updated : q));
			transport = await getTransport();
			if (!scrubbing) scrubValue = transport.position_ms || 0;
			if (!updated.available) {
				sourceError = updated.note || `${sourceTypeLabel(sourceType)} is a stub — sinks stay linked`;
			}
		} catch (err) {
			sourceError = err instanceof Error ? err.message : String(err);
			await refresh().catch(() => {});
		}
	}

	/** @param {import('$lib/api.js').SourceType} sourceType */
	async function addStubSong(sourceType) {
		try {
			sourceError = '';
			const item = await addQueueItem({
				source_type: sourceType,
				title:
					sourceType === 'spotify'
						? 'Spotify track (stub)'
						: sourceType === 'apple'
							? 'Apple Music track (stub)'
							: 'Input cue (stub)'
			});
			queue = [...queue, item];
			sourceError = item.note || `${sourceTypeLabel(sourceType)} stub added — switch type anytime`;
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
			transport = await getTransport();
		} catch (err) {
			sourceError = err instanceof Error ? err.message : String(err);
		}
	}

	onMount(() => {
		let cancelled = false;
		let timer;

		refresh()
			.then(() => {
				if (cancelled) return;
				timer = setInterval(async () => {
					try {
						transport = await getTransport();
						if (!scrubbing) scrubValue = transport.position_ms || 0;
					} catch (err) {
						console.error(err);
					}
				}, 1000);
			})
			.catch((err) => {
				apiError = err instanceof Error ? err.message : String(err);
				console.error(err);
			});

		return () => {
			cancelled = true;
			clearInterval(timer);
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
				Active song is a {sourceTypeLabel(transport?.source_type)} stub — sink graph stays up; local
				playback resumes when you switch the song back to Local.
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

	<div class="split">
		<section class="panel devices-panel" aria-labelledby="devices-heading">
			<div class="panel-head">
				<h2 id="devices-heading">Devices</h2>
				<p class="hint">Up to 6 BT sinks · tap a row for delay &amp; volume</p>
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
								<span class="device-title">{d.name}</span>
								<span class="device-sub">
									{d.hci || '—'} · {d.mac || 'no MAC'} · delay {d.delay_ms} ms · vol {Math.round(d.volume * 100)}%
								</span>
							</span>
							<span class="status-pill {d.connected ? 'linked' : 'missing'}">
								{d.connected ? 'linked' : 'missing'}{d.enabled ? '' : ' · off'}
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
					{selected.hci || 'hci?'} · {selected.mac || 'MAC unset'} · {selected.connected ? 'linked' : 'missing'}
				</p>

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

	<section class="panel queue-panel" aria-labelledby="queue-heading">
		<div class="panel-head">
			<h2 id="queue-heading">Song queue</h2>
			<p class="hint">Per-song source · switch without tearing down sinks</p>
		</div>
		<div class="stub-actions">
			<button type="button" class="btn btn-ghost btn-small" onclick={() => addStubSong('spotify')}>
				+ Spotify stub
			</button>
			<button type="button" class="btn btn-ghost btn-small" onclick={() => addStubSong('apple')}>
				+ Apple stub
			</button>
			<button type="button" class="btn btn-ghost btn-small" onclick={() => addStubSong('input')}>
				+ Input stub
			</button>
		</div>
		<ul class="queue-list">
			{#each queue as item (item.id)}
				<li class="queue-row" class:active={item.id === activeQueueId}>
					<button type="button" class="queue-main" onclick={() => chooseQueueItem(item.id)}>
						<span class="source-title">{item.title}</span>
						<span class="source-sub">
							{item.locator || item.source_id || item.id}
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
								<option value={t}>{sourceTypeLabel(t)}{t === 'local' ? '' : ' (stub)'}</option>
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
			<p class="hint">Local ready · Spotify / Apple / input stubs</p>
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
								onclick={() => chooseSource(s.id)}
							>
								<span>
									<span class="source-title">{s.title}</span>
									<span class="source-sub">
										{s.type}{s.id === transport?.source_id ? ' · active' : ''}{s.note ? ` · ${s.note}` : ''}
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
</main>
