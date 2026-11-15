<script>
	import { onMount } from 'svelte';
	import {
		connectWifi,
		fmtMs,
		forgetWifiNetwork,
		getHealth,
		getTransport,
		getWifiStatus,
		listDevices,
		listSources,
		listWifiNetworks,
		patchDevice,
		putTransport,
		saveWifiNetwork,
		setActiveSource
	} from '$lib/api.js';

	/** @type {import('$lib/api.js').Device[]} */
	let devices = $state([]);
	/** @type {import('$lib/api.js').Source[]} */
	let sources = $state([]);
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

	/** @type {Record<string, ReturnType<typeof setTimeout>>} */
	const patchTimers = {};

	const selected = $derived(devices.find((d) => d.id === selectedId) ?? null);
	const playing = $derived(transport?.state === 'playing');
	const wifiIpLabel = $derived(wifi?.ips?.length ? wifi.ips.join(', ') : '—');
	const healthLabel = $derived.by(() => {
		if (apiError) return 'API offline';
		if (!health) return '…';
		const label = health.backend === 'mock' ? 'mock' : 'pipewire';
		return `${label} · ${health.enabled_count}/${health.sink_count} sinks`;
	});

	async function refreshWifi() {
		const [w, nets] = await Promise.all([getWifiStatus(), listWifiNetworks()]);
		wifi = w;
		wifiNetworks = nets;
	}

	async function refresh() {
		const [h, d, t, s] = await Promise.all([
			getHealth(),
			listDevices(),
			getTransport(),
			listSources()
		]);
		health = h;
		devices = d;
		transport = t;
		sources = s;
		apiError = '';
		if (!selectedId && d.length) selectedId = d[0].id;
		if (!scrubbing) scrubValue = t.position_ms || 0;
		await refreshWifi().catch(() => {});
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
		transport = await putTransport(body);
		if (!scrubbing) scrubValue = transport.position_ms || 0;
	}

	/** @param {string} id */
	async function chooseSource(id) {
		transport = await setActiveSource(id);
		if (!scrubbing) scrubValue = transport.position_ms || 0;
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
			<p class="now-playing">{transport?.title || 'No track'}</p>
		</div>
		<div class="transport-controls">
			<button type="button" class="btn" aria-label="Play" disabled={playing} onclick={() => setTransport({ state: 'playing' })}>
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

	<section class="panel sources-panel" aria-labelledby="sources-heading">
		<div class="panel-head">
			<h2 id="sources-heading">Sources</h2>
			<p class="hint">Local files now · stream / input / movie later</p>
		</div>
		<ul class="source-list">
			{#each sources as s (s.id)}
				<li>
					<button
						type="button"
						class="source-row"
						disabled={!s.available}
						onclick={() => chooseSource(s.id)}
					>
						<span>
							<span class="source-title">{s.title}</span>
							<span class="source-sub">
								{s.type}{s.id === transport?.source_id ? ' · active' : ''}{s.note ? ` · ${s.note}` : ''}
							</span>
						</span>
						<span class="status-pill {s.available ? 'linked' : 'missing'}">
							{s.available ? 'ready' : 'later'}
						</span>
					</button>
				</li>
			{/each}
		</ul>
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
