<script>
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';

	/** @type {{ section?: 'now' | 'outputs' | 'inputs' | 'streaming' | 'queue' | 'wifi' | 'profiles' }} */
	let { section = 'now' } = $props();

	const TABS = [
		{ id: 'now', href: '/', label: 'Now' },
		{ id: 'outputs', href: '/outputs/', label: 'Outputs' },
		{ id: 'inputs', href: '/inputs/', label: 'Inputs' },
		{ id: 'streaming', href: '/streaming/', label: 'Stream' },
		{ id: 'queue', href: '/queue/', label: 'Queue' },
		{ id: 'wifi', href: '/wifi/', label: 'Wi‑Fi' },
		{ id: 'profiles', href: '/profiles/', label: 'Profiles' }
	];
	import {
		CAPTURE_PATHS,
		HOST_KINDS,
		SOURCE_TYPES,
		addInput,
		addQueueItem,
		addStreamingTrack,
		applyProfile,
		assignDeviceUser,
		capturePathLabel,
		clearBluetoothDiscovery,
		connectBluetooth,
		connectWifi,
		createProfile,
		deleteInput,
		deleteProfile,
		deleteQueueItem,
		discoverDevices,
		discoverInputs,
		fmtMs,
		forgetWifiNetwork,
		getHealth,
		SPOTIFY_LOGIN_HREF,
		fetchSpotifyAuthorizeUrl,
		getNowPlaying,
		getStreamingCredentials,
		getStreamingStatus,
		saveStreamingCredentials,
		getTransport,
		getWifiStatus,
		listDevices,
		listInputs,
		listProfiles,
		listQueue,
		listSources,
		listWifiNetworks,
		logoutSpotify,
		patchDevice,
		patchInput,
		patchProfile,
		patchQueueItem,
		putDevicesConfig,
		putTransport,
		saveWifiNetwork,
		searchStreaming,
		mediaPreviewUrl,
		setActiveProfile,
		setActiveQueueItem,
		setActiveSource,
		sourceTypeLabel,
		spaPollCadence,
		startBluetoothScan,
		stopBluetoothScan,
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
	/** @type {import('$lib/api.js').DiscoveredCastSink[]} */
	let discoveredCast = $state([]);
	/** @type {import('$lib/api.js').BluetoothAdapter[]} */
	let btAdapters = $state([]);
	let btAdapterCapacity = $state(0);
	let btSoftWarn = $state('');
	/** @type {import('$lib/api.js').Device | null} */
	let sppDefault = $state(null);
	/** @type {Set<string>} */
	let setupBtNames = $state(new Set());
	/** @type {Set<string>} */
	let setupCastNames = $state(new Set());
	let setupSppEnabled = $state(true);
	let setupSppHost = $state('0.0.0.0');
	let setupSppPort = $state(1234);
	let setupSppRate = $state(48000);
	let setupSppChannels = $state(1);
	let setupBusy = $state(false);
	let setupMessage = $state('');
	let setupDetail = $state('');
	let setupConfigOk = $state(true);
	let btScanning = $state(false);
	let btScanBusy = $state(false);
	let btConnectBusy = $state('');
	/** Preferred HCI for next Connect (empty = auto). */
	let preferredHci = $state('');
	/** @type {ReturnType<typeof setInterval> | null} */
	let btScanPoll = null;

	/** @type {import('$lib/api.js').VolumeProfile[]} */
	let profiles = $state([]);
	/** @type {string | null} */
	let activeProfileId = $state(null);
	/** @type {Record<string, string>} */
	let deviceAssignments = $state({});
	/** @type {Record<string, number>} */
	let learnedDeviceGains = $state({});
	let newProfileName = $state('');
	let newProfileLevel = $state(80);
	let profileBusy = $state(false);
	let profileMessage = $state('');

	/** @type {import('$lib/api.js').StreamingStatus | null} */
	let streaming = $state(null);
	/** @type {import('$lib/api.js').StreamingCredentialsStatus | null} */
	let streamCreds = $state(null);
	/** @type {import('$lib/api.js').NowPlaying | null} */
	let nowPlaying = $state(null);
	/** @type {'spotify' | 'youtube'} */
	let streamProvider = $state('spotify');
	let streamQuery = $state('');
	/** @type {import('$lib/api.js').StreamingSearchHit[]} */
	let streamHits = $state([]);
	let streamBusy = $state(false);
	let streamMessage = $state('');
	let credClientId = $state('');
	let credClientSecret = $state('');
	let credRedirectUri = $state('');
	let credYoutubeKey = $state('');
	let credBusy = $state(false);
	let credMessage = $state('');
	let guidesOpen = $state(false);

	/** @type {Record<string, ReturnType<typeof setTimeout>>} */
	const patchTimers = {};

	/** Last server position sample — UI interpolates between transport polls while playing. */
	let clockAnchorMs = 0;
	let clockAnchorAt = 0;

	const selected = $derived(devices.find((d) => d.id === selectedId) ?? null);
	const outputDevices = $derived(devices);
	const activeProfile = $derived(
		profiles.find((p) => p.id === activeProfileId) ?? null
	);
	const playing = $derived(transport?.state === 'playing');
	const wifiIpLabel = $derived(wifi?.ips?.length ? wifi.ips.join(', ') : '—');
	/** Phone-friendly hub URL for Wi‑Fi / home card + QR (http://PI_IP:PORT). */
	const wifiLanUrl = $derived(
		wifi?.lan_url ||
			(wifi?.ips?.length
				? `http://${wifi.ips[0]}:${wifi.port || 8080}`
				: '')
	);
	const wifiQrSrc = $derived(
		wifiLanUrl ? `/api/wifi/lan-qr.svg?u=${encodeURIComponent(wifiLanUrl)}` : ''
	);
	const activeQueueId = $derived(transport?.queue_item_id ?? null);
	const activeQueueItem = $derived(queue.find((q) => q.id === activeQueueId) ?? null);
	/** Play disabled when the active cue is not ready (unavailable / unbound live). */
	const activeUnavailable = $derived(Boolean(activeQueueItem && !activeQueueItem.available));
	const liveHosts = $derived(inputs.filter((h) => h.connected && h.endpoint));
	/** @type {string} */
	let devicePickId = $state('');
	$effect(() => {
		if (!liveHosts.length) {
			devicePickId = '';
			return;
		}
		if (!liveHosts.some((h) => h.id === devicePickId)) {
			devicePickId = liveHosts[0].id;
		}
	});
	const activeIsVideo = $derived(
		Boolean(
			transport?.media_kind === 'video' ||
				(activeQueueItem?.media_kind === 'video' && activeQueueItem?.source_type === 'local')
		)
	);
	const activeVideoUrl = $derived(
		activeIsVideo ? mediaPreviewUrl(transport?.source_id || activeQueueItem?.source_id) : null
	);

	/** @type {HTMLVideoElement | null} */
	let videoPreviewEl = $state(null);

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

	/** Keep muted HTML preview roughly in sync with feeder transport (audio is mpv → sinks). */
	function syncVideoPreview() {
		const el = videoPreviewEl;
		if (!el || !activeVideoUrl) return;
		const targetSec = displayedPositionMs() / 1000;
		if (Number.isFinite(el.duration) && el.duration > 0) {
			const clamped = Math.min(el.duration, Math.max(0, targetSec));
			if (Math.abs(el.currentTime - clamped) > 0.45) {
				try {
					el.currentTime = clamped;
				} catch {
					/* seek before metadata */
				}
			}
		}
		if (transport?.state === 'playing') {
			el.play().catch(() => {});
		} else {
			el.pause();
		}
	}

	$effect(() => {
		// Depend on transport + scrub for preview sync.
		void transport?.state;
		void transport?.position_ms;
		void scrubValue;
		void activeVideoUrl;
		syncVideoPreview();
	});
	const healthLabel = $derived.by(() => {
		if (apiError) return 'Offline';
		if (!health) return '…';
		const mode = health.backend === 'mock' ? 'Demo' : 'Live';
		const detail = health.detail || '';
		const readyM = detail.match(/READY=([^;]+)/);
		const missM = detail.match(/MISSING=([^;]+)/);
		const ready = readyM?.[1]?.trim();
		const missing = missM?.[1]?.trim();
		const readiness =
			missing && missing !== '—'
				? ' · missing'
				: ready
					? ' · ready'
					: '';
		const healthyAdapters = health.healthy_adapter_count;
		const adapterN = health.adapter_count;
		const adapters =
			typeof healthyAdapters === 'number' && typeof adapterN === 'number'
				? ` · BT ${healthyAdapters}/${adapterN}`
				: '';
		return `${mode} · ${health.enabled_count}/${health.sink_count} outs · ${inputs.length}/4 ins${adapters}${readiness}`;
	});

	const healthyAdapterCount = $derived(btAdapters.filter((a) => a.healthy !== false).length);
	const usedHcis = $derived(
		new Set(
			devices
				.filter((d) => d.kind === 'bluetooth' && d.hci)
				.map((d) => String(d.hci).toLowerCase())
		)
	);
	const freeHealthyAdapters = $derived(
		btAdapters.filter(
			(a) => a.healthy !== false && !usedHcis.has(String(a.hci).toLowerCase())
		)
	);
	const hasUsbAdapters = $derived(
		btAdapters.some((a) => a.onboard === false || a.bus === 'usb')
	);

	/** @param {import('$lib/api.js').Device} d */
	function sinkStatusLabel(d) {
		if (!d.enabled) return 'off';
		if (d.kind === 'network_spp' || d.kind === 'cast') {
			return d.connected ? 'READY' : 'MISSING';
		}
		return d.connected ? 'READY' : 'MISSING';
	}

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

	/**
	 * @param {import('$lib/api.js').DiscoveredBluetoothSink[]} rows
	 */
	function applyBtRows(rows) {
		discoveredBt = rows || [];
		const names = new Set();
		for (const bt of discoveredBt) {
			if (
				bt.configured_id ||
				devices.some((d) => d.kind === 'bluetooth' && d.name === bt.name && d.enabled)
			) {
				names.add(bt.name);
			}
		}
		for (const d of devices) {
			if (d.kind !== 'bluetooth') continue;
			if (discoveredBt.some((bt) => bt.name === d.name) && d.enabled) {
				names.add(d.name);
			}
		}
		setupBtNames = names;
	}

	/**
	 * @param {import('$lib/api.js').DiscoveredCastSink[]} rows
	 */
	function applyCastRows(rows) {
		discoveredCast = rows || [];
		const names = new Set();
		for (const c of discoveredCast) {
			if (
				c.configured_id ||
				devices.some((d) => d.kind === 'cast' && d.name === c.name && d.enabled)
			) {
				names.add(c.name);
			}
		}
		for (const d of devices) {
			if (d.kind !== 'cast') continue;
			if (discoveredCast.some((c) => c.name === d.name) && d.enabled) {
				names.add(d.name);
			}
		}
		setupCastNames = names;
	}

	/**
	 * @param {{
	 *   adapters?: import('$lib/api.js').BluetoothAdapter[];
	 *   adapter_capacity?: number;
	 *   soft_warn?: string;
	 * }} payload
	 */
	function applyAdapterStatus(payload) {
		btAdapters = payload.adapters || [];
		btAdapterCapacity =
			typeof payload.adapter_capacity === 'number'
				? payload.adapter_capacity
				: btAdapters.filter((a) => a.healthy !== false).length;
		btSoftWarn = payload.soft_warn || '';
	}

	async function refreshDiscovery() {
		const disc = await discoverDevices();
		applyBtRows(disc.bluetooth || []);
		applyCastRows(disc.cast || []);
		applyAdapterStatus(disc);
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
	}

	/** @param {import('$lib/api.js').BluetoothScanStatus} status */
	function applyScanStatus(status) {
		btScanning = Boolean(status.scanning);
		applyBtRows(status.bluetooth || []);
		applyAdapterStatus(status);
		if (status.detail) setupDetail = status.detail;
	}

	function stopBtScanPoll() {
		if (btScanPoll) {
			clearInterval(btScanPoll);
			btScanPoll = null;
		}
	}

	async function onStartBtScan() {
		if (btScanBusy) return;
		btScanBusy = true;
		setupMessage = '';
		try {
			applyScanStatus(await startBluetoothScan());
			setupMessage = 'Bluetooth scan started';
			stopBtScanPoll();
			btScanPoll = setInterval(async () => {
				try {
					applyScanStatus(await startBluetoothScan());
				} catch (err) {
					console.error(err);
				}
			}, 4000);
		} catch (err) {
			setupMessage = err instanceof Error ? err.message : String(err);
		} finally {
			btScanBusy = false;
		}
	}

	async function onStopBtScan() {
		if (btScanBusy) return;
		btScanBusy = true;
		setupMessage = '';
		try {
			stopBtScanPoll();
			applyScanStatus(await stopBluetoothScan());
			setupMessage = 'Bluetooth scan stopped';
		} catch (err) {
			setupMessage = err instanceof Error ? err.message : String(err);
		} finally {
			btScanBusy = false;
		}
	}

	async function onClearDiscovery() {
		if (btScanBusy) return;
		btScanBusy = true;
		setupMessage = '';
		try {
			applyScanStatus(await clearBluetoothDiscovery());
			setupMessage = 'Cleared discovery — connected devices kept';
		} catch (err) {
			setupMessage = err instanceof Error ? err.message : String(err);
		} finally {
			btScanBusy = false;
		}
	}

	/**
	 * @param {import('$lib/api.js').DiscoveredBluetoothSink} bt
	 */
	async function onConnectBt(bt) {
		if (!bt.mac || btConnectBusy) return;
		btConnectBusy = bt.mac;
		setupMessage = '';
		try {
			/** @type {{ mac: string, name?: string, hci?: string, enable_as_output?: boolean }} */
			const body = {
				mac: bt.mac,
				name: bt.name,
				enable_as_output: true
			};
			const want = preferredHci.trim().toLowerCase();
			if (want && freeHealthyAdapters.some((a) => a.hci === want)) {
				body.hci = want;
			}
			const res = await connectBluetooth(body);
			if (res.bluetooth) applyBtRows(res.bluetooth);
			applyAdapterStatus(res);
			if (res.ok) {
				setupMessage = res.device
					? `Connected ${bt.name}${res.device.hci ? ` · ${res.device.hci}` : ''}`
					: res.soft_warn || res.detail || `Connected ${bt.name} (output not enabled)`;
				devices = await listDevices();
				if (res.device?.id) selectedId = res.device.id;
				health = await getHealth().catch(() => health);
				if (res.soft_warn && !res.device) btSoftWarn = res.soft_warn;
				if (res.device?.hci && preferredHci === res.device.hci) {
					preferredHci = '';
				}
			} else {
				setupMessage = res.detail || `Connect failed for ${bt.name}`;
				if (res.soft_warn) btSoftWarn = res.soft_warn;
			}
		} catch (err) {
			setupMessage = err instanceof Error ? err.message : String(err);
		} finally {
			btConnectBusy = '';
		}
	}

	/**
	 * @param {import('$lib/api.js').VolumeProfilesState} state
	 */
	function applyProfileState(state) {
		profiles = state.profiles || [];
		activeProfileId = state.active_id ?? null;
		deviceAssignments = state.assignments || {};
		learnedDeviceGains = state.device_gains || {};
	}

	async function refreshProfiles() {
		applyProfileState(await listProfiles());
	}

	async function refresh() {
		const [h, d, t, s, q, i, st, np] = await Promise.all([
			getHealth(),
			listDevices(),
			getTransport(),
			listSources(),
			listQueue(),
			listInputs(),
			getStreamingStatus().catch(() => null),
			getNowPlaying().catch(() => null)
		]);
		health = h;
		devices = d;
		sources = s;
		queue = q;
		inputs = i;
		if (st) streaming = st;
		if (np) nowPlaying = np;
		apiError = '';
		if (!selectedId && d.length) selectedId = d[0].id;
		applyTransport(t);
		await refreshStreamCreds().catch(() => {});
		await refreshProfiles().catch(() => {});
		await refreshWifi().catch(() => {});
		await refreshDiscovery().catch((err) => {
			setupMessage = err instanceof Error ? err.message : String(err);
		});
		await refreshCaptureDiscovery().catch(() => {});
	}

	async function refreshStreaming() {
		streaming = await getStreamingStatus();
		await refreshStreamCreds().catch(() => {});
	}

	async function refreshStreamCreds() {
		const c = await getStreamingCredentials();
		streamCreds = c;
		// Prefill redirect URI (not a secret). Leave secret fields blank so
		// empty submit keeps existing values.
		if (c?.spotify_redirect_uri?.value) {
			credRedirectUri = c.spotify_redirect_uri.value;
		}
	}

	async function onSaveStreamCreds() {
		if (credBusy) return;
		credBusy = true;
		credMessage = '';
		/** @type {import('$lib/api.js').StreamingCredentialsPut} */
		const body = {};
		const id = credClientId.trim();
		const secret = credClientSecret.trim();
		let redirect = credRedirectUri.trim();
		const yt = credYoutubeKey.trim();
		// Refuse empty port (http://host:/path) — backend also normalizes.
		if (redirect && /:\/\/[^/]+:\//.test(redirect)) {
			credMessage =
				'Redirect URI has an empty port (host:/path) — use :8080 (or your MUSYNC_PORT).';
			credBusy = false;
			return;
		}
		if (id) body.spotify_client_id = id;
		if (secret) body.spotify_client_secret = secret;
		if (redirect) body.spotify_redirect_uri = redirect;
		if (yt) body.youtube_api_key = yt;
		try {
			const res = await saveStreamingCredentials(body);
			streamCreds = res;
			if (res.spotify_redirect_uri?.value) {
				credRedirectUri = res.spotify_redirect_uri.value;
			}
			// Clear secret inputs after save so raw values leave the DOM.
			credClientSecret = '';
			credYoutubeKey = '';
			credClientId = '';
			streaming = await getStreamingStatus();
			if (!res.saved) {
				credMessage = 'Nothing to save — enter at least one value (empty soft-fails).';
			} else {
				const n = res.updated_keys?.length || 0;
				credMessage = `Saved ${n} key${n === 1 ? '' : 's'} to ${res.env_path} (never committed).`;
			}
		} catch (err) {
			credMessage = err instanceof Error ? err.message : String(err);
		} finally {
			credBusy = false;
		}
	}

	async function refreshNowPlaying() {
		nowPlaying = await getNowPlaying();
	}

	/** Availability pill — never label as “stub”. */
	/** @param {{ available?: boolean, note?: string | null, source_type?: string, type?: string }} row */
	function availabilityLabel(row) {
		if (row.available) return 'ready';
		const note = (row.note || '').toLowerCase();
		if (note.includes('on-device') || note.includes('session')) return 'on-device';
		if (note.includes('drm') || note.includes('airplay') || note.includes('shairport'))
			return 'unavailable';
		return 'unavailable';
	}

	async function onConnectSpotify() {
		streamMessage = '';
		try {
			const res = await fetchSpotifyAuthorizeUrl();
			if (!res.ok) {
				streamMessage = res.error || 'Spotify credentials missing — see docs/spotify-apple.md';
				return;
			}
			window.location.assign(res.url || SPOTIFY_LOGIN_HREF);
		} catch (err) {
			// Fall back to same-origin redirect if JSON helper fails.
			streamMessage = err instanceof Error ? err.message : String(err);
			window.location.assign(SPOTIFY_LOGIN_HREF);
		}
	}

	async function onStreamSearch() {
		const q = streamQuery.trim();
		if (!q || streamBusy) return;
		if (streamProvider === 'spotify' && !streaming?.spotify?.configured) {
			streamMessage =
				'Spotify not configured — set MUSYNC_SPOTIFY_CLIENT_ID/SECRET in ~/musync.env, restart, then Connect on Streaming';
			return;
		}
		if (streamProvider === 'youtube' && !streaming?.youtube?.configured) {
			streamMessage =
				'YouTube not configured — set MUSYNC_YOUTUBE_API_KEY in ~/musync.env (docs/youtube.md)';
			return;
		}
		if (streamProvider === 'spotify' && !streaming?.spotify?.authenticated) {
			streamMessage = 'Connect Spotify on the Streaming tab, then search here';
			return;
		}
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
			const raw = err instanceof Error ? err.message : String(err);
			const lower = raw.toLowerCase();
			if (lower.includes('insufficient client scope') || lower.includes('missing required scopes')) {
				streamMessage =
					raw +
					' → Streaming tab: Disconnect (optional) → Reconnect Spotify and approve permissions.';
			} else {
				streamMessage = raw;
			}
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
				album: hit.album ?? undefined,
				cover_url: hit.cover_url ?? undefined,
				duration_ms: hit.duration_ms ?? undefined,
				activate: true
			});
			queue = await listQueue();
			applyTransport(await getTransport());
			sources = await listSources();
			await refreshNowPlaying().catch(() => {});
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

	/** @param {string} name */
	function toggleSetupCast(name) {
		const next = new Set(setupCastNames);
		if (next.has(name)) next.delete(name);
		else next.add(name);
		setupCastNames = next;
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
			for (const c of discoveredCast) {
				if (!setupCastNames.has(c.name)) continue;
				entries.push({
					id: c.configured_id || undefined,
					name: c.name,
					kind: 'cast',
					enabled: true,
					cast_uuid: c.uuid || undefined,
					cast_host: c.host || undefined,
					cast_port: c.port || 8009,
					listen_host: '0.0.0.0',
					listen_port: 8090,
					sample_rate: 48000,
					channels: 2,
					delay_ms: 300
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
	 * Debounced PATCH /api/devices/{id} — delay/volume/gain live on outputs (MDAS-24).
	 * @param {string} id
	 * @param {Partial<Pick<import('$lib/api.js').Device, 'delay_ms' | 'volume' | 'gain' | 'mute' | 'enabled' | 'kind' | 'listen_host' | 'listen_port' | 'sample_rate' | 'channels'>>} partial
	 */
	function queueDevicePatch(id, partial) {
		if (!id) return;
		devices = devices.map((d) => (d.id === id ? { ...d, ...partial } : d));

		clearTimeout(patchTimers[id]);
		patchTimers[id] = setTimeout(async () => {
			try {
				const updated = await patchDevice(id, partial);
				devices = devices.map((d) => (d.id === id ? updated : d));
				// Volume set by an assigned user re-fits S_u / G_d on the hub.
				if (partial.volume != null && deviceAssignments[id]) {
					await refreshProfiles().catch(() => {});
				}
			} catch (err) {
				console.error(err);
				await refresh().catch(() => {});
			}
		}, 180);
	}

	async function onCreateProfile() {
		const name = newProfileName.trim();
		if (!name || profileBusy) return;
		profileBusy = true;
		profileMessage = '';
		try {
			const created = await createProfile({
				name,
				preferred_level: Math.max(0, Math.min(1, newProfileLevel / 100))
			});
			newProfileName = '';
			await refreshProfiles();
			profileMessage = `Created ${created.name}`;
		} catch (err) {
			profileMessage = err instanceof Error ? err.message : String(err);
		} finally {
			profileBusy = false;
		}
	}

	/**
	 * @param {string} id
	 * @param {boolean} [apply]
	 */
	async function onSelectProfile(id, apply = true) {
		if (profileBusy) return;
		profileBusy = true;
		profileMessage = '';
		try {
			const state = await setActiveProfile(id);
			applyProfileState(state);
			if (apply) {
				devices = await listDevices();
				profileMessage = `Applied ${profiles.find((p) => p.id === id)?.name || id}`;
			}
		} catch (err) {
			profileMessage = err instanceof Error ? err.message : String(err);
		} finally {
			profileBusy = false;
		}
	}

	async function onClearProfile() {
		if (profileBusy) return;
		profileBusy = true;
		profileMessage = '';
		try {
			applyProfileState(await setActiveProfile(null));
			profileMessage = 'Cleared active profile (outputs unchanged)';
		} catch (err) {
			profileMessage = err instanceof Error ? err.message : String(err);
		} finally {
			profileBusy = false;
		}
	}

	/**
	 * Assign which listening profile/user owns an output (presets via S_u × G_d).
	 * @param {string} deviceId
	 * @param {string} profileId
	 */
	async function onAssignDeviceUser(deviceId, profileId) {
		if (profileBusy) return;
		profileBusy = true;
		profileMessage = '';
		try {
			const res = await assignDeviceUser({
				device_id: deviceId,
				profile_id: profileId || null,
				apply_suggested: true
			});
			applyProfileState(res.state);
			if (res.device) {
				devices = devices.map((d) => (d.id === res.device.id ? res.device : d));
			}
			profileMessage = res.detail || 'Assignment updated';
		} catch (err) {
			profileMessage = err instanceof Error ? err.message : String(err);
		} finally {
			profileBusy = false;
		}
	}

	/** @param {string} id */
	async function onDeleteProfile(id) {
		if (profileBusy) return;
		profileBusy = true;
		profileMessage = '';
		try {
			await deleteProfile(id);
			await refreshProfiles();
			profileMessage = 'Profile deleted';
		} catch (err) {
			profileMessage = err instanceof Error ? err.message : String(err);
		} finally {
			profileBusy = false;
		}
	}

	/**
	 * @param {string} id
	 * @param {number} pct
	 */
	async function onProfileLevel(id, pct) {
		const level = Math.max(0, Math.min(1, pct / 100));
		profiles = profiles.map((p) =>
			p.id === id ? { ...p, preferred_level: level } : p
		);
		clearTimeout(patchTimers[`profile:${id}`]);
		patchTimers[`profile:${id}`] = setTimeout(async () => {
			try {
				const updated = await patchProfile(id, { preferred_level: level });
				profiles = profiles.map((p) => (p.id === id ? updated : p));
				if (activeProfileId === id) {
					devices = await listDevices();
				}
			} catch (err) {
				console.error(err);
				await refreshProfiles().catch(() => {});
			}
		}, 180);
	}

	/** @param {string} id */
	async function onReapplyProfile(id) {
		if (profileBusy) return;
		profileBusy = true;
		profileMessage = '';
		try {
			devices = await applyProfile(id);
			profileMessage = 'Re-applied to outputs';
		} catch (err) {
			profileMessage = err instanceof Error ? err.message : String(err);
		} finally {
			profileBusy = false;
		}
	}

	/**
	 * @param {{ state?: import('$lib/api.js').TransportState, position_ms?: number }} body
	 */
	async function setTransport(body) {
		try {
			sourceError = '';
			applyTransport(await putTransport(body));
			nowPlaying = await getNowPlaying().catch(() => nowPlaying);
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
			nowPlaying = await getNowPlaying().catch(() => nowPlaying);
			const active = queue.find((q) => q.id === t.queue_item_id);
			if (active && !active.available) {
				sourceError =
					active.note ||
					`${sourceTypeLabel(active.source_type)} unavailable — switch a queue row to Local or +Device to play`;
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
			nowPlaying = await getNowPlaying().catch(() => nowPlaying);
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
			nowPlaying = await getNowPlaying().catch(() => nowPlaying);
			if (!updated.available) {
				sourceError =
					updated.note || `${sourceTypeLabel(sourceType)} unavailable — sinks stay linked`;
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
			nowPlaying = await getNowPlaying().catch(() => nowPlaying);
			if (!updated.available) {
				sourceError = updated.note || 'Host-bound source unavailable — sinks stay linked';
			}
		} catch (err) {
			sourceError = err instanceof Error ? err.message : String(err);
		}
	}

	/** Add a playable live input cue from a currently live BT A2DP / USB host. */
	async function addDeviceFromLive(hostId) {
		const id = (hostId || devicePickId || liveHosts[0]?.id || '').trim();
		if (!id) {
			sourceError =
				'No live inputs — Refresh inputs (BT A2DP / USB-A / HDMI), then +Device';
			return;
		}
		const host = inputs.find((h) => h.id === id);
		if (!host || !host.connected || !host.endpoint) {
			sourceError = 'Pick a live BT A2DP / USB / HDMI host from the +Device list';
			return;
		}
		try {
			sourceError = '';
			const item = await addQueueItem({
				source_type: 'input',
				input_id: id,
				title: `Live · ${host.name}`
			});
			queue = [...queue, item];
			if (!item.available) {
				sourceError = item.note || 'Input not live yet — Refresh inputs';
			}
			devicePickId = id;
		} catch (err) {
			sourceError = err instanceof Error ? err.message : String(err);
		}
	}

	/** @param {'spotify' | 'youtube' | 'input'} sourceType */
	async function addQueueShortcut(sourceType) {
		try {
			sourceError = '';
			if (sourceType === 'spotify') {
				if (!streaming?.spotify?.configured) {
					sourceError =
						'Spotify unavailable — set CLIENT_ID/SECRET in ~/musync.env, then Connect on Streaming';
					return;
				}
				if (!streaming?.spotify?.authenticated) {
					sourceError = 'Connect Spotify on the Streaming tab, then search here on Queue';
					return;
				}
				sourceError = '';
				streamProvider = 'spotify';
				streamMessage = 'Search Spotify below, then Queue & play — that adds a real track';
				goto('/queue/');
				return;
			}
			if (sourceType === 'youtube') {
				if (!streaming?.youtube?.configured) {
					sourceError =
						'YouTube unavailable — set MUSYNC_YOUTUBE_API_KEY in ~/musync.env (docs/youtube.md)';
					return;
				}
				streamProvider = 'youtube';
				streamMessage = 'Search YouTube above, then Queue & play — yt-dlp → ffmpeg → feeder';
				goto('/queue/');
				return;
			}
			// + Live — prefer AirPlay host when receiving, else first live host
			const airplayLive = liveHosts.find((h) => h.capture_path === 'airplay');
			const hostId = airplayLive?.id || liveHosts[0]?.id;
			if (!hostId) {
				sourceError =
					'No live input — Refresh inputs (AirPlay / BT / USB / HDMI), then + Device';
				return;
			}
			await addDeviceFromLive(hostId);
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
				? `${discoveredCapture.length} live source${discoveredCapture.length === 1 ? '' : 's'} (USB/HDMI/AirPlay auto-bound when found)`
				: 'No bluez_input / USB-A / HDMI / AirPlay sources yet — plug a USB sound card or HDMI capture, start shairport-sync, or pair phone as A2DP source';
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
			const kind = /** @type {import('$lib/api.js').HostKind} */ (
				path === 'usb' ? 'pc' : path === 'hdmi' ? 'other' : 'phone'
			);
			let host = inputs.find((h) => h.id === src.configured_id) ||
				inputs.find((h) => h.name === src.name) ||
				inputs.find((h) => h.capture_path === path && !h.connected);
			if (host) {
				host = await patchInput(host.id, {
					name: src.name,
					kind,
					capture_path: path,
					endpoint,
					connected: true,
					armed: true
				});
				inputs = inputs.map((h) => (h.id === host.id ? host : h));
			} else if (inputs.length < 4) {
				host = await addInput({
					name: src.name,
					kind,
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
		/** @type {ReturnType<typeof setTimeout> | undefined} */
		let transportTimer;
		/** @type {ReturnType<typeof setTimeout> | undefined} */
		let nowPlayingTimer;
		/** @type {ReturnType<typeof setInterval> | undefined} */
		let tickTimer;

		function clearAdaptivePolls() {
			clearTimeout(transportTimer);
			clearTimeout(nowPlayingTimer);
			transportTimer = undefined;
			nowPlayingTimer = undefined;
		}

		function scheduleAdaptivePolls() {
			clearAdaptivePolls();
			const cadence = spaPollCadence({
				playing: transport?.state === 'playing',
				hidden: document.hidden
			});
			if (cadence.paused) return;

			const pollTransport = async () => {
				if (cancelled || document.hidden) return;
				try {
					applyTransport(await getTransport());
				} catch (err) {
					console.error(err);
				}
				if (cancelled || document.hidden) return;
				const next = spaPollCadence({
					playing: transport?.state === 'playing',
					hidden: document.hidden
				});
				if (!next.paused) {
					transportTimer = setTimeout(pollTransport, next.transportMs);
				}
			};

			const pollNowPlaying = async () => {
				if (cancelled || document.hidden) return;
				try {
					nowPlaying = await getNowPlaying().catch(() => nowPlaying);
				} catch (err) {
					console.error(err);
				}
				if (cancelled || document.hidden) return;
				const next = spaPollCadence({
					playing: transport?.state === 'playing',
					hidden: document.hidden
				});
				if (!next.paused) {
					nowPlayingTimer = setTimeout(pollNowPlaying, next.nowPlayingMs);
				}
			};

			transportTimer = setTimeout(pollTransport, cadence.transportMs);
			nowPlayingTimer = setTimeout(pollNowPlaying, cadence.nowPlayingMs);
		}

		function onVisibility() {
			if (cancelled) return;
			if (document.hidden) {
				clearAdaptivePolls();
				return;
			}
			// Immediate catch-up when the tab becomes visible again.
			getTransport()
				.then(applyTransport)
				.catch(() => {});
			getNowPlaying()
				.then((np) => {
					nowPlaying = np;
				})
				.catch(() => {});
			scheduleAdaptivePolls();
		}

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
				scheduleAdaptivePolls();
				document.addEventListener('visibilitychange', onVisibility);
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
			clearAdaptivePolls();
			clearInterval(tickTimer);
			document.removeEventListener('visibilitychange', onVisibility);
			stopBtScanPoll();
			for (const t of Object.values(patchTimers)) clearTimeout(t);
		};
	});
</script>

<div class="atmosphere" aria-hidden="true"></div>

<div class="app-shell" data-section={section}>
	<header class="top">
		<div class="brand-block">
			<a class="brand" href="/" aria-label="MuSync home">MuSync</a>
			<p class="tagline">Headphones in sync</p>
		</div>
		<div
			class="health"
			data-ok={apiError ? 'false' : health ? String(Boolean(health.ok)) : undefined}
			title={health?.detail || apiError || ''}
		>
			{healthLabel}
		</div>
	</header>

	{#if section !== 'now'}
	<aside class="sticky-chrome" aria-label="Now playing">
		<div class="sticky-cover">
			{#if nowPlaying?.cover_url || transport?.cover_url}
				<img
					src={nowPlaying?.cover_url || transport?.cover_url || ''}
					alt=""
					width="48"
					height="48"
				/>
			{:else}
				<div class="sticky-cover-empty" aria-hidden="true"></div>
			{/if}
		</div>
		<div class="sticky-meta">
			<p class="sticky-title">{nowPlaying?.title || transport?.title || 'No track'}</p>
			<p class="sticky-artist">{nowPlaying?.artist || transport?.artist || 'Unknown artist'}</p>
		</div>
		<div class="sticky-transport">
			<button
				type="button"
				class="btn btn-icon"
				aria-label="Play"
				disabled={playing || activeUnavailable}
				onclick={() => setTransport({ state: 'playing' })}
			>
				▶
			</button>
			<button
				type="button"
				class="btn btn-icon btn-ghost"
				aria-label="Pause"
				disabled={!playing}
				onclick={() => setTransport({ state: 'paused' })}
			>
				❚❚
			</button>
			<span class="sticky-time">{fmtMs(scrubValue)} · {fmtMs(transport?.duration_ms || 0)}</span>
		</div>
	</aside>
	{/if}

	<nav class="tab-nav" aria-label="Primary">
		{#each TABS as tab}
			<a
				href={tab.href}
				class="tab-link"
				class:active={section === tab.id}
				aria-current={section === tab.id ? 'page' : undefined}
			>
				{tab.label}
			</a>
		{/each}
	</nav>

	<main class="layout" data-section={section}>
{#if section === 'now'}
		<section class="panel panel-primary transport-panel" aria-labelledby="now-playing-heading">
		<div class="panel-head">
			<h2 id="now-playing-heading">Now playing</h2>
			<p class="hint">
				{sourceTypeLabel(nowPlaying?.source_type || transport?.source_type)}
				{#if transport?.state}
					· {transport.state}
				{/if}
			</p>
		</div>
		<div class="now-playing-block">
			{#if nowPlaying?.cover_url || transport?.cover_url}
				<img
					class="cover-art"
					src={nowPlaying?.cover_url || transport?.cover_url || ''}
					alt={(nowPlaying?.album || transport?.album || 'Album') + ' cover'}
					width="88"
					height="88"
				/>
			{:else}
				<div class="cover-art cover-art-empty" aria-hidden="true"></div>
			{/if}
			<div class="now-playing-text">
				<p class="now-playing-title">
					{nowPlaying?.title || transport?.title || 'No track'}
				</p>
				<p class="now-playing-meta">
					{nowPlaying?.artist || transport?.artist || 'Unknown artist'}
				</p>
				{#if nowPlaying?.album || transport?.album}
					<p class="now-playing-meta muted">{nowPlaying?.album || transport?.album}</p>
				{/if}
				<div class="now-playing-chips">
					{#if nowPlaying?.source_type || transport?.source_type}
						<span class="type-chip">{sourceTypeLabel(nowPlaying?.source_type || transport?.source_type)}</span>
					{/if}
					{#if activeIsVideo}
						<span class="type-chip">Video</span>
					{/if}
				</div>
			</div>
		</div>
		{#if nowPlaying?.note}
			<p class="hint muted" role="status">{nowPlaying.note}</p>
		{/if}
		{#if activeUnavailable}
			<p class="stub-banner" role="status">
				Unavailable
				{#if transport?.source_type === 'input'}
					— Refresh inputs / pair BT A2DP, USB-A, or HDMI capture, then +Device and Play.
				{:else if transport?.source_type === 'spotify' || transport?.source_type === 'apple'}
					— Connect Spotify &amp; search, or use +Device / Local (Apple DRM limits hub PCM).
				{:else}
					— switch to Local or +Device from a live input; sinks stay linked.
				{/if}
			</p>
		{/if}
		{#if sourceError}
			<p class="stub-banner warn" role="alert">{sourceError}</p>
		{/if}
		{#if activeVideoUrl}
			<div class="video-preview">
				<video
					bind:this={videoPreviewEl}
					src={activeVideoUrl}
					muted
					playsinline
					preload="metadata"
					aria-label="Muted video preview — audio plays on MuSync sinks"
				></video>
				<p class="hint">Muted preview · audio → musync_feeder / sinks</p>
			</div>
		{/if}
		<div class="transport-controls">
			<button
				type="button"
				class="btn"
				aria-label="Play"
				disabled={playing || activeUnavailable}
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
				disabled={activeUnavailable}
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

{/if}

{#if section === 'wifi'}
	<div class="zone" id="zone-network">
		<p class="zone-label">Network</p>

		<section class="panel wifi-panel" aria-labelledby="wifi-heading">
			<div class="panel-head">
				<h2 id="wifi-heading">Wi-Fi</h2>
				<p class="hint">Home Wi-Fi or phone hotspot · open hub URL on the same LAN</p>
			</div>

			<div class="wifi-lan-card" data-ok={wifi ? String(Boolean(wifi.connected)) : undefined}>
				<div class="wifi-lan-copy">
					<span class="wifi-label">LAN URL</span>
					{#if wifiLanUrl}
						<a class="wifi-lan-url" href={wifiLanUrl}>{wifiLanUrl}</a>
						<button
							type="button"
							class="btn btn-ghost btn-small wifi-copy-btn"
							onclick={async () => {
								try {
									await navigator.clipboard.writeText(wifiLanUrl);
									wifiMessage = `Copied ${wifiLanUrl}`;
								} catch {
									wifiMessage = wifiLanUrl;
								}
							}}
						>
							Copy
						</button>
					{:else}
						<code class="wifi-lan-url wifi-lan-missing">http://PI_IP:{wifi?.port || 8080}</code>
						<p class="wifi-line muted-line">Join a network below to reveal the live URL.</p>
					{/if}
					<p class="wifi-line muted-line">
						{#if wifi?.connected}
							{wifi.ssid || 'associated'}
							{#if wifi.interface}
								· {wifi.interface}
							{/if}
							· IPs {wifiIpLabel}
						{:else}
							Not associated · IPs {wifiIpLabel}
						{/if}
					</p>
					{#if wifi?.mdns_hostname}
						<p class="wifi-line muted-line">
							mDNS UI: http://{wifi.mdns_hostname}:{wifi?.port || 8080} (not for Spotify OAuth)
						</p>
					{/if}
					{#if wifi?.detail}
						<p class="wifi-detail">{wifi.detail}</p>
					{/if}
				</div>
				{#if wifiQrSrc}
					<figure class="wifi-qr">
						<img src={wifiQrSrc} width="168" height="168" alt={`QR code for ${wifiLanUrl}`} />
						<figcaption>Scan to open</figcaption>
					</figure>
				{/if}
			</div>

			<form
				class="wifi-form"
				onsubmit={(e) => {
					e.preventDefault();
					onJoinWifi();
				}}
			>
				<div class="wifi-fields">
					<label class="field">
						<span>SSID / network name</span>
						<input
							type="text"
							name="ssid"
							autocomplete="off"
							maxlength="32"
							placeholder="Home Wi-Fi or phone hotspot"
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
							placeholder="Blank if already saved"
							bind:value={wifiPassword}
							disabled={wifiBusy}
						/>
					</label>
				</div>
				<p class="hint muted wifi-join-note">
					Joins any saved or entered network — home router or phone personal hotspot.
				</p>
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

			{#if wifiNetworks.length}
				<ul class="wifi-list">
					{#each wifiNetworks as n (n.ssid)}
						<li class="wifi-row">
							<span>
								<span class="device-title">{n.ssid}</span>
								<span class="device-sub">
									{n.active ? 'active' : 'saved'}{n.has_password ? '' : ' · no key'}
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
					{/each}
				</ul>
			{:else}
				<p class="wifi-empty">No saved networks</p>
			{/if}
		</section>
	</div>

{/if}

{#if section === 'outputs'}
	<div class="zone" id="zone-outputs">
		<p class="zone-label">Outputs</p>

		<section class="panel setup-panel" aria-labelledby="setup-heading">
			<div class="panel-head">
				<h2 id="setup-heading">Bluetooth search</h2>
				<p class="hint">
					{btScanning ? 'Scanning…' : 'Scan · connect · clear'}
					{#if btScanning}
						<span class="status-pill linked scan-pill">live</span>
					{/if}
					{#if btAdapterCapacity}
						· up to {btAdapterCapacity} A2DP
					{/if}
				</p>
			</div>
			{#if btAdapters.length}
				<ul class="adapter-list" aria-label="Bluetooth adapters">
					{#each btAdapters as adapter (adapter.hci)}
						{@const used = usedHcis.has(String(adapter.hci).toLowerCase())}
						<li
							class="adapter-chip"
							class:adapter-dead={adapter.healthy === false}
							class:adapter-used={used && adapter.healthy !== false}
							title={adapter.detail || adapter.name || adapter.hci}
						>
							<span class="adapter-hci">{adapter.hci}</span>
							{#if adapter.onboard}
								<span class="adapter-tag">onboard</span>
							{:else}
								<span class="adapter-tag">USB</span>
							{/if}
							{#if adapter.usb_id}
								<span class="adapter-usb">{adapter.usb_id}</span>
							{/if}
							<span
								class="status-pill {adapter.healthy === false
									? 'missing'
									: used
										? 'warn'
										: 'linked'}"
							>
								{adapter.healthy === false
									? 'dead'
									: used
										? 'in use'
										: adapter.powered === false
											? 'off'
											: 'ready'}
							</span>
						</li>
					{/each}
				</ul>
				{#if freeHealthyAdapters.length > 1}
					<label class="adapter-assign">
						<span class="adapter-assign-label">Assign HCI on Connect</span>
						<select bind:value={preferredHci} class="adapter-select">
							<option value="">Auto (onboard first)</option>
							{#each freeHealthyAdapters as a (a.hci)}
								<option value={a.hci}>
									{a.hci}{a.onboard ? ' · onboard' : ' · USB'}{a.usb_id
										? ` · ${a.usb_id}`
										: ''}
								</option>
							{/each}
						</select>
					</label>
				{/if}
				{#if hasUsbAdapters}
					<p class="hint adapter-untested">
						USB HCI path is soft-fail ready — multi-dongle A2DP is untested without hardware.
					</p>
				{/if}
			{:else}
				<p class="hint adapter-empty">
					Adapters appear when BlueZ is up — onboard alone is fine (1 A2DP sink).
				</p>
			{/if}
			{#if btSoftWarn && healthyAdapterCount <= 1}
				<p class="setup-warn">{btSoftWarn}</p>
			{/if}
			{#if !setupConfigOk}
				<p class="setup-warn">Config missing — save outputs below to write devices.yaml.</p>
			{/if}
			<div class="setup-actions bt-scan-actions">
				<button
					type="button"
					class="btn"
					disabled={btScanBusy || btScanning}
					onclick={() => onStartBtScan()}
				>
					{btScanning ? 'Scanning…' : 'Start scan'}
				</button>
				<button
					type="button"
					class="btn btn-ghost"
					disabled={btScanBusy || !btScanning}
					onclick={() => onStopBtScan()}
				>
					Stop
				</button>
				<button
					type="button"
					class="btn btn-ghost"
					disabled={btScanBusy || discoveredBt.length === 0}
					onclick={() => onClearDiscovery()}
				>
					Clear list
				</button>
				<button
					type="button"
					class="btn btn-ghost"
					disabled={setupBusy || btScanBusy}
					onclick={() => refreshDiscovery()}
				>
					Refresh
				</button>
			</div>
			{#if discoveredBt.length === 0}
				<p class="hint">No Bluetooth devices yet — Start scan (or pair on the Pi, then Refresh).</p>
			{:else}
				<ul class="setup-list bt-discover-list">
					{#each discoveredBt as bt (bt.mac || bt.name)}
						<li class="bt-discover-row">
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
										{#if bt.mac}
											· {bt.mac}
										{/if}
									</span>
								</span>
							</label>
							<button
								type="button"
								class="btn btn-small"
								disabled={!bt.mac || btConnectBusy === bt.mac || bt.connected}
								onclick={() => onConnectBt(bt)}
							>
								{bt.connected
									? 'Connected'
									: btConnectBusy === bt.mac
										? 'Connecting…'
										: 'Connect'}
							</button>
						</li>
					{/each}
				</ul>
			{/if}

			<div class="setup-col cast-output-col">
				<h3 class="setup-sub">Cast / Nest Mini</h3>
				<p class="hint">
					Wi‑Fi discovery (mDNS). Cast latency is higher than BT — default delay 300&nbsp;ms.
					Stream URL uses <code>{wifiIpLabel}:8090</code>.
				</p>
				{#if discoveredCast.length === 0}
					<p class="hint">No Cast devices yet — Refresh on the same Wi‑Fi as Nest Mini.</p>
				{:else}
					<ul class="setup-list">
						{#each discoveredCast as c (c.uuid || c.host || c.name)}
							<li>
								<label class="check setup-check">
									<input
										type="checkbox"
										checked={setupCastNames.has(c.name)}
										onchange={() => toggleSetupCast(c.name)}
									/>
									<span>
										<strong>{c.name}</strong>
										<span class="device-sub">
											{c.model || 'Cast'}
											{#if c.host}
												· {c.host}
											{/if}
										</span>
									</span>
								</label>
							</li>
						{/each}
					</ul>
				{/if}
			</div>

			<div class="setup-col spp-output-col">
				<h3 class="setup-sub">Phone SPP output</h3>
				<p class="hint spp-phone-hint">
					Phone app host = <code>{wifiIpLabel}</code> (not 0.0.0.0) · port below
				</p>
				<label class="check setup-check">
					<input type="checkbox" bind:checked={setupSppEnabled} />
					<span>
						<strong>Enable TCP listen</strong>
						<span class="device-sub">Simple Protocol Player → Pi IP:port</span>
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

			<div class="setup-actions">
				<button type="button" class="btn" disabled={setupBusy} onclick={() => saveDeviceSetup()}>
					{setupBusy ? 'Saving…' : 'Save outputs'}
				</button>
			</div>
			{#if setupMessage}
				<p class="setup-msg">{setupMessage}</p>
			{/if}
			{#if setupDetail}
				<p class="hint setup-detail">{setupDetail}</p>
			{/if}
		</section>

		<section class="panel devices-panel" aria-labelledby="devices-heading">
			<div class="panel-head">
				<h2 id="devices-heading">Connected outputs</h2>
				<p class="hint">
					Delay · volume · gain (calibration)
					{#if activeProfile}
						· profile {activeProfile.name}
					{/if}
				</p>
			</div>
			<ul class="device-list output-control-list">
				{#each outputDevices as d (d.id)}
					<li class="output-card" class:selected={d.id === selectedId}>
						<button
							type="button"
							class="device-row output-row-head"
							aria-pressed={d.id === selectedId}
							onclick={() => {
								selectedId = d.id === selectedId ? null : d.id;
							}}
						>
							<span>
								<span class="device-title">
									{d.name}
									{#if d.kind === 'network_spp'}
										<span class="type-chip">SPP</span>
									{:else if d.kind === 'cast'}
										<span class="type-chip">Cast</span>
									{:else}
										<span class="type-chip">BT</span>
									{/if}
								</span>
								<span class="device-sub">
									{#if d.kind === 'network_spp'}
										:{d.listen_port || 1234} · {d.sample_rate || 48000}/{d.channels || 1}
									{:else if d.kind === 'cast'}
										{d.cast_host || 'Wi‑Fi'} · HTTP :{d.listen_port || 8090} · delay≥BT
									{:else if d.mac}
										{d.mac}{#if d.hci} · {d.hci}{/if}
									{:else if d.hci}
										{d.hci}
									{:else}
										Bluetooth A2DP
									{/if}
								</span>
							</span>
							<span
								class="status-pill {sinkStatusLabel(d) === 'READY'
									? 'linked'
									: sinkStatusLabel(d) === 'off'
										? ''
										: 'missing'}"
							>
								{sinkStatusLabel(d)}
							</span>
						</button>
						<div class="output-controls">
							<label class="field compact-field">
								<span>User on this output</span>
								<select
									value={deviceAssignments[d.id] || ''}
									disabled={profileBusy || !profiles.length}
									onchange={(e) => {
										onAssignDeviceUser(
											d.id,
											/** @type {HTMLSelectElement} */ (e.currentTarget).value
										);
									}}
								>
									<option value="">— unassigned —</option>
									{#each profiles as p (p.id)}
										<option value={p.id}>{p.name}</option>
									{/each}
								</select>
								{#if learnedDeviceGains[d.id]}
									<small class="gain-hint"
										>G_d {(learnedDeviceGains[d.id] ?? 1).toFixed(2)}</small
									>
								{/if}
							</label>
							<label class="field compact-field">
								<span>Delay <em>{d.delay_ms} ms</em></span>
								<input
									type="range"
									min="0"
									max={d.kind === 'cast' ? 2000 : 500}
									step="5"
									value={d.delay_ms}
									disabled={!d.enabled}
									oninput={(e) => {
										queueDevicePatch(d.id, {
											delay_ms: Number(/** @type {HTMLInputElement} */ (e.currentTarget).value)
										});
									}}
								/>
							</label>
							<label class="field compact-field">
								<span>Volume <em>{Math.round(d.volume * 100)}%</em></span>
								<input
									type="range"
									min="0"
									max="100"
									step="1"
									value={Math.round(d.volume * 100)}
									disabled={!d.enabled}
									oninput={(e) => {
										const pct = Number(/** @type {HTMLInputElement} */ (e.currentTarget).value);
										queueDevicePatch(d.id, { volume: pct / 100 });
									}}
								/>
							</label>
							<label class="field compact-field">
								<span
									>Gain <em>{(d.gain ?? 1).toFixed(2)}×</em>
									<small class="gain-hint">headphone cal</small></span
								>
								<input
									type="range"
									min="10"
									max="200"
									step="5"
									value={Math.round((d.gain ?? 1) * 100)}
									disabled={!d.enabled}
									oninput={(e) => {
										const pct = Number(/** @type {HTMLInputElement} */ (e.currentTarget).value);
										queueDevicePatch(d.id, { gain: pct / 100 });
									}}
								/>
							</label>
							<div class="toggles">
								<label class="check">
									<input
										type="checkbox"
										checked={d.mute}
										onchange={(e) => {
											queueDevicePatch(d.id, {
												mute: /** @type {HTMLInputElement} */ (e.currentTarget).checked
											});
										}}
									/>
									<span>Mute</span>
								</label>
								<label class="check">
									<input
										type="checkbox"
										checked={d.enabled}
										onchange={(e) => {
											queueDevicePatch(d.id, {
												enabled: /** @type {HTMLInputElement} */ (e.currentTarget).checked
											});
										}}
									/>
									<span>Enabled</span>
								</label>
							</div>
						</div>
						{#if selectedId === d.id && d.kind === 'network_spp'}
							<div class="spp-inline-fields output-spp-extra">
								<label class="field">
									<span>Listen host</span>
									<input
										type="text"
										value={d.listen_host || '0.0.0.0'}
										oninput={(e) => {
											queueDevicePatch(d.id, {
												listen_host: /** @type {HTMLInputElement} */ (e.currentTarget).value
											});
										}}
									/>
								</label>
								<label class="field">
									<span>Port</span>
									<input
										type="number"
										min="1"
										max="65535"
										value={d.listen_port || 1234}
										oninput={(e) => {
											queueDevicePatch(d.id, {
												listen_port: Number(
													/** @type {HTMLInputElement} */ (e.currentTarget).value
												)
											});
										}}
									/>
								</label>
								<p class="hint">Phone → {wifiIpLabel}:{d.listen_port || 1234}</p>
							</div>
						{/if}
					</li>
				{:else}
					<li class="empty-group">No outputs yet — scan/connect BT or enable Phone SPP, then Save</li>
				{/each}
			</ul>
		</section>
	</div>


{/if}

{#if section === 'profiles'}
	<div class="zone" id="zone-profiles">
		<p class="zone-label">Profiles</p>
		<section class="panel profiles-panel" aria-labelledby="profiles-heading">
			<div class="panel-head">
				<h2 id="profiles-heading">Listening profiles</h2>
				<p class="hint">
					Silent-disco prefs · preferred × gain
					{#if activeProfile}
						· active <span class="status-pill linked">{activeProfile.name}</span>
					{/if}
				</p>
			</div>
			<p class="hint muted">
				Named users share one feeder; preferred × gain applies explicitly. Assign a user on each
				output to learn S_u × G_d and preset new IEMs. Delay stays independent.
			</p>
			<form
				class="profile-create"
				onsubmit={(e) => {
					e.preventDefault();
					onCreateProfile();
				}}
			>
				<label class="field">
					<span>Name</span>
					<input
						type="text"
						bind:value={newProfileName}
						placeholder="e.g. Nish"
						maxlength="64"
						autocomplete="off"
					/>
				</label>
				<label class="field compact-field">
					<span>Preferred <em>{newProfileLevel}%</em></span>
					<input type="range" min="0" max="100" step="1" bind:value={newProfileLevel} />
				</label>
				<button type="submit" class="btn" disabled={profileBusy || !newProfileName.trim()}>
					{profileBusy ? '…' : 'Create'}
				</button>
			</form>
			{#if profiles.length}
				<ul class="profile-list">
					{#each profiles as p (p.id)}
						<li class="profile-row" class:active={p.id === activeProfileId}>
							<div class="profile-main">
								<span class="source-title"
									>{p.name}
									<small class="gain-hint" title="Learned relative sensitivity S_u"
										>S {(p.sensitivity ?? 1).toFixed(2)}</small
									></span
								>
								<label class="field compact-field">
									<span>Preferred <em>{Math.round(p.preferred_level * 100)}%</em></span>
									<input
										type="range"
										min="0"
										max="100"
										step="1"
										value={Math.round(p.preferred_level * 100)}
										oninput={(e) => {
											onProfileLevel(
												p.id,
												Number(/** @type {HTMLInputElement} */ (e.currentTarget).value)
											);
										}}
									/>
								</label>
							</div>
							<div class="profile-actions">
								{#if p.id === activeProfileId}
									<button
										type="button"
										class="btn btn-small"
										disabled={profileBusy}
										onclick={() => onReapplyProfile(p.id)}
									>
										Re-apply
									</button>
									<button
										type="button"
										class="btn btn-ghost btn-small"
										disabled={profileBusy}
										onclick={() => onClearProfile()}
									>
										Clear
									</button>
								{:else}
									<button
										type="button"
										class="btn btn-small"
										disabled={profileBusy}
										onclick={() => onSelectProfile(p.id)}
									>
										Use
									</button>
								{/if}
								<button
									type="button"
									class="btn btn-ghost btn-small"
									disabled={profileBusy}
									onclick={() => onDeleteProfile(p.id)}
								>
									Delete
								</button>
							</div>
						</li>
					{/each}
				</ul>
			{:else}
				<p class="hint">No profiles yet — create one to map a preferred level across outputs.</p>
			{/if}
			{#if profileMessage}
				<p class="setup-msg">{profileMessage}</p>
			{/if}
		</section>

	</div>
{/if}

{#if section === 'inputs'}
	<div class="zone" id="zone-inputs">
		<p class="zone-label">Inputs</p>

		<section class="panel inputs-panel" aria-labelledby="inputs-heading">
			<div class="panel-head">
				<h2 id="inputs-heading">Inputs</h2>
				<p class="hint">Phone / PC / HDMI hosts · up to 4 · no delay/volume</p>
			</div>
			<p class="hint muted">
				Pi 5 USB-C PD is power-only — PC audio needs a USB-A sound card/interface (not the PD port).
				HDMI: plug a capture dongle, then Refresh. AirPlay: run shairport-sync, then Refresh
				(docs/airplay-receive.md). Phone: pair as A2DP <em>source</em>, then Refresh.
			</p>
			<div class="stub-actions">
				<button
					type="button"
					class="btn btn-ghost btn-small"
					disabled={captureBusy}
					onclick={() => refreshCaptureDiscovery()}
				>
					{captureBusy ? 'Scanning…' : 'Refresh inputs'}
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
									{capturePathLabel(src.capture_path || 'bt_a2dp')}
									{#if src.pw_name || src.pw_node}
										· {src.pw_name || src.pw_node}
									{/if}
								</span>
							</div>
							<button type="button" class="btn btn-small" onclick={() => bindCaptureSource(src)}>
								{src.configured_id ? 'Re-bind' : 'Use'}
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
								{capturePathLabel(host.capture_path)}
								{#if host.endpoint}
									· {host.endpoint}
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
					<li class="empty-group">No hosts yet — register or bind a discovered source</li>
				{/each}
			</ul>
			{#if captureDetail}
				<p class="hint muted">{captureDetail}</p>
			{/if}
		</section>
	</div>

{/if}

{#if section === 'streaming' || section === 'queue'}
	<div class="zone" id="zone-sources">
		<p class="zone-label">Sources</p>

{#if section === 'streaming'}
		<section class="panel streaming-panel" aria-labelledby="streaming-heading">
			<div class="panel-head">
				<h2 id="streaming-heading">Streaming</h2>
				<p class="hint">API keys → ~/musync.env · OAuth Connect · search on Queue</p>
			</div>

			<section class="stream-creds" aria-labelledby="stream-creds-heading">
				<div class="panel-head stream-creds-head">
					<h3 id="stream-creds-heading">Developer credentials</h3>
					<p class="hint muted">
						Saved to <code>{streamCreds?.env_path || '~/musync.env'}</code> — never committed or
						returned raw (masked below). Blank fields keep existing values; empty save soft-fails.
					</p>
				</div>
				<form
					class="cred-form"
					onsubmit={(e) => {
						e.preventDefault();
						onSaveStreamCreds();
					}}
				>
					<label class="cred-field">
						<span>Spotify Client ID</span>
						<input
							type="text"
							autocomplete="off"
							spellcheck="false"
							placeholder={streamCreds?.spotify_client_id?.set
								? streamCreds.spotify_client_id.masked || '•••• set'
								: 'from developer.spotify.com'}
							bind:value={credClientId}
							aria-label="Spotify Client ID"
						/>
					</label>
					<label class="cred-field">
						<span>Spotify Client Secret</span>
						<input
							type="password"
							autocomplete="new-password"
							spellcheck="false"
							placeholder={streamCreds?.spotify_client_secret?.set
								? '•••• set — leave blank to keep'
								: 'from developer.spotify.com'}
							bind:value={credClientSecret}
							aria-label="Spotify Client Secret"
						/>
					</label>
					<label class="cred-field">
						<span>Spotify redirect URI</span>
						<input
							type="url"
							autocomplete="off"
							spellcheck="false"
							placeholder="http://&lt;Pi-IP&gt;:8080/api/streaming/spotify/callback"
							bind:value={credRedirectUri}
							aria-label="Spotify redirect URI"
						/>
					</label>
					<label class="cred-field">
						<span>YouTube Data API key</span>
						<input
							type="password"
							autocomplete="new-password"
							spellcheck="false"
							placeholder={streamCreds?.youtube_api_key?.set
								? '•••• set — leave blank to keep'
								: 'from Google Cloud Console'}
							bind:value={credYoutubeKey}
							aria-label="YouTube API key"
						/>
					</label>
					<div class="cred-actions">
						<button type="submit" class="btn btn-small" disabled={credBusy}>
							{credBusy ? 'Saving…' : 'Save to musync.env'}
						</button>
						<span class="status-pill {streamCreds?.spotify_configured ? 'linked' : 'missing'}">
							Spotify {streamCreds?.spotify_configured ? 'keys set' : 'keys empty'}
						</span>
						<span class="status-pill {streamCreds?.youtube_configured ? 'linked' : 'missing'}">
							YouTube {streamCreds?.youtube_configured ? 'key set' : 'key empty'}
						</span>
					</div>
				</form>
				{#if credMessage}
					<p class="hint" role="status">{credMessage}</p>
				{/if}
				<details class="cred-guides" bind:open={guidesOpen}>
					<summary>Short setup guides (Spotify Dashboard · Google Cloud)</summary>
					<div class="guide-grid">
						<div class="guide-block">
							<strong>Spotify Developer Dashboard</strong>
							<ol>
								<li>
									Open
									<a
										href="https://developer.spotify.com/dashboard"
										target="_blank"
										rel="noopener noreferrer">developer.spotify.com/dashboard</a
									>
									→ Create app
								</li>
								<li>Copy <em>Client ID</em> and <em>Client Secret</em> into the fields above</li>
								<li>
									Dashboard redirect URIs (register <em>both</em>; Spotify forbids
									<code>localhost</code> / <code>musync.local</code>):
									<code>http://127.0.0.1:8080/api/streaming/spotify/callback</code>
									and
									<code>http://127.0.0.1:8081/api/streaming/spotify/callback</code>.
									Set env to the API port you run (not Vite :5173). UI may use
									<code>http://musync.local:8080</code> — OAuth still needs
									<code>127.0.0.1</code> or Pi IP.
								</li>
								<li>Save here → <strong>Connect Spotify</strong> below → search on Queue</li>
							</ol>
						</div>
						<div class="guide-block">
							<strong>Google Cloud (YouTube Data API)</strong>
							<ol>
								<li>
									Open
									<a
										href="https://console.cloud.google.com/"
										target="_blank"
										rel="noopener noreferrer">console.cloud.google.com</a
									>
									→ create/select a project
								</li>
								<li>APIs &amp; Services → Library → enable <em>YouTube Data API v3</em></li>
								<li>Credentials → Create credentials → API key (optional: restrict to that API)</li>
								<li>Paste the key above → Save → search YouTube on Queue</li>
							</ol>
						</div>
					</div>
				</details>
			</section>

			<div class="stream-status">
				<div class="stream-card">
					<strong>Spotify</strong>
					<span class="status-pill {streaming?.spotify?.authenticated ? 'linked' : 'missing'}">
						{streaming?.spotify?.authenticated
							? streaming.spotify.display_name || 'connected'
							: streaming?.spotify?.configured
								? 'ready to connect'
								: 'not configured'}
					</span>
					{#if streaming?.spotify?.authenticated}
						<button
							type="button"
							class="btn btn-small"
							disabled={streamBusy}
							onclick={() => onConnectSpotify()}
						>
							{streaming?.spotify?.missing_scopes?.length ? 'Reconnect Spotify' : 'Reconnect'}
						</button>
						<button
							type="button"
							class="btn btn-ghost btn-small"
							disabled={streamBusy}
							onclick={() => onSpotifyLogout()}
						>
							Disconnect
						</button>
						{#if streaming?.spotify?.missing_scopes?.length}
							<p class="hint stream-error" role="alert">
								Token missing playback scopes ({streaming.spotify.missing_scopes.join(', ')}).
								Click <strong>Reconnect Spotify</strong> and approve the consent dialog.
							</p>
						{:else}
							<p class="hint muted">
								Connected — search &amp; queue tracks on the <a href="/queue/">Queue</a> tab.
							</p>
						{/if}
						<p class="hint muted scopes-line" title="Granted OAuth scopes from last Connect">
							Granted scopes:
							{#if streaming?.spotify?.scopes?.length}
								<code>{streaming.spotify.scopes.join(' ')}</code>
							{:else}
								<code>(none stored — Reconnect to refresh)</code>
							{/if}
							{#if streaming?.spotify?.search_market}
								· search market <code>{streaming.spotify.search_market}</code>
							{/if}
							{#if streaming?.spotify?.search_limit_max}
								· max <code>{streaming.spotify.search_limit_max}</code>
							{/if}
						</p>
					{:else}
						<button
							type="button"
							class="btn btn-small"
							disabled={streamBusy || !streaming?.spotify?.configured}
							onclick={() => onConnectSpotify()}
						>
							Connect Spotify
						</button>
						<p class="hint muted">
							OAuth consent for search + playback. Save Client ID/Secret above first (stored in
							<code>~/musync.env</code>, never logged). Then use Queue to search.
						</p>
					{/if}
					<p class="hint muted scopes-line">
						OAuth callback (Connect = loopback):
						<code>{streaming?.spotify?.oauth_loopback_uri || streaming?.spotify?.redirect_uri || 'http://127.0.0.1:8080/api/streaming/spotify/callback'}</code>
					</p>
					<p class="hint muted">
						librespot:
						<span
							class="status-pill {streaming?.spotify?.librespot_running
								? 'linked'
								: streaming?.spotify?.librespot_available
									? 'warn'
									: 'missing'}"
						>
							{streaming?.spotify?.librespot_running
								? `running · ${streaming.spotify.librespot_name || 'MuSync'}`
								: streaming?.spotify?.librespot_available
									? `on PATH · ${streaming.spotify.librespot_name || 'MuSync'} (starts on Play)`
									: 'missing — Pi hub PCM needs librespot'}
						</span>
					</p>
					{#if streaming?.spotify?.librespot_available === false}
						<p class="hint muted">
							Laptop mock can search/auth without librespot; on the Pi install
							<code>librespot</code> for Premium Connect → <code>musync_feeder</code>.
						</p>
					{/if}
					{#if streaming?.spotify?.oauth_hint}
						<p class="hint muted">{streaming.spotify.oauth_hint}</p>
					{/if}
					{#if !streaming?.spotify?.configured}
						<p class="hint muted">
							Not configured — paste Client ID/Secret (+ redirect URI) above, or edit
							<code>~/musync.env</code>. Empty keys soft-fail (local/AirPlay still work).
						</p>
					{/if}
					{#if streaming?.spotify?.note}
						<p class="hint muted">{streaming.spotify.note}</p>
					{/if}
				</div>
				<div class="stream-card">
					<strong>AirPlay</strong>
					<span
						class="status-pill {streaming?.airplay?.receiving
							? 'linked'
							: streaming?.airplay?.configured
								? 'linked'
								: 'missing'}"
					>
						{streaming?.airplay?.receiving
							? 'receiving'
							: streaming?.airplay?.configured
								? 'ready'
								: 'setup needed'}
					</span>
					<p class="hint muted">
						Receive from iPhone/Mac via <code>shairport-sync</code> → PipeWire →
						<code>musync_feeder</code>. No MusicKit / Apple Music search on the hub.
					</p>
					{#if streaming?.airplay?.source}
						<p class="hint muted">
							Source: <code>{streaming.airplay.source}</code>
							{#if streaming.airplay.receiver_name}
								· receiver {streaming.airplay.receiver_name}{/if}
						</p>
					{/if}
					{#if !streaming?.airplay?.configured}
						<ol class="hint muted airplay-setup">
							<li>Install <code>shairport-sync</code> (PipeWire/Pulse output)</li>
							<li>Optional: <code>MUSYNC_AIRPLAY_SOURCE</code> in <code>~/musync.env</code></li>
							<li>Inputs → Refresh inputs → Play live AirPlay host</li>
							<li>AirPlay audio to the Pi from iPhone/Mac</li>
						</ol>
					{/if}
					{#if streaming?.airplay?.note}
						<p class="hint muted">{streaming.airplay.note}</p>
					{/if}
					<p class="hint muted">
						Setup details: <code>docs/airplay-receive.md</code>
					</p>
				</div>
				<div class="stream-card">
					<strong>YouTube</strong>
					<span class="status-pill {streaming?.youtube?.configured ? 'linked' : 'missing'}">
						{streaming?.youtube?.configured
							? streaming.youtube.playback_mode === 'ytdlp_ffmpeg'
								? 'search + play'
								: 'API key · need yt-dlp/ffmpeg'
							: 'not configured'}
					</span>
					<p class="hint muted">
						API key saved above (<code>MUSYNC_YOUTUBE_API_KEY</code>). Hub PCM:
						<code>yt-dlp</code> → <code>ffmpeg</code> → <code>musync_feeder</code>.
					</p>
					{#if streaming?.youtube?.ytdlp_available === false}
						<p class="hint muted">Install <code>yt-dlp</code> on the Pi for hub PCM.</p>
					{:else if streaming?.youtube?.ytdlp_outdated}
						<p class="hint" role="status">
							Upgrade yt-dlp{streaming.youtube.ytdlp_version
								? ` (have ${streaming.youtube.ytdlp_version})`
								: ''} — <code>sudo yt-dlp -U</code> or
							<code>pipx upgrade yt-dlp</code> (see guide).
						</p>
					{/if}
					{#if streaming?.youtube?.ffmpeg_available === false}
						<p class="hint muted">Install <code>ffmpeg</code> on the Pi for hub PCM.</p>
					{/if}
					{#if !streaming?.youtube?.configured}
						<p class="hint muted">
							Not configured — paste a YouTube Data API key above (see guide). Empty key soft-fails.
						</p>
					{/if}
					{#if streaming?.youtube?.last_error}
						<p class="hint" role="status">
							Play soft-fail: {streaming.youtube.last_error}
						</p>
					{/if}
					{#if streaming?.youtube?.note}
						<p class="hint muted">{streaming.youtube.note}</p>
					{/if}
				</div>
			</div>
			{#if streamMessage}
				<p class="hint" role="status">{streamMessage}</p>
			{/if}
		</section>

{/if}

{#if section === 'queue'}
		<section class="panel queue-panel" aria-labelledby="queue-heading">
			<div class="panel-head">
				<h2 id="queue-heading">Song queue</h2>
				<p class="hint">Search Spotify/YouTube · AirPlay via Inputs · sinks stay linked</p>
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
					<select bind:value={streamProvider} aria-label="Streaming provider">
						<option value="spotify">Spotify</option>
						<option value="youtube">YouTube</option>
					</select>
				</label>
				<input
					type="search"
					placeholder="Search Spotify or YouTube"
					bind:value={streamQuery}
					aria-label="Search tracks"
				/>
				<button type="submit" class="btn btn-small" disabled={streamBusy || !streamQuery.trim()}>
					Search
				</button>
			</form>
			{#if streamMessage}
				<p
					class="hint"
					class:muted={!/fail|error|forbidden|rejected|not configured|connect spotify/i.test(
						streamMessage
					)}
					class:stream-error={/fail|error|forbidden|rejected|not configured|connect spotify/i.test(
						streamMessage
					)}
					role="status"
				>
					{streamMessage}
				</p>
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
					<li class="empty-group">
						{#if streaming?.spotify?.authenticated || streaming?.youtube?.configured}
							Search Spotify or YouTube — AirPlay uses Inputs → live host
						{:else}
							Connect Spotify / set YouTube key on Streaming · AirPlay: Inputs → Refresh
						{/if}
					</li>
				{/each}
			</ul>
			<div class="stub-actions queue-add-actions">
				<div class="device-add">
					<label class="sr-only" for="device-pick">Live input for +Device</label>
					<select
						id="device-pick"
						class="type-select device-pick"
						bind:value={devicePickId}
						disabled={!liveHosts.length}
						aria-label="Live input host"
					>
						{#if liveHosts.length}
							{#each liveHosts as h (h.id)}
								<option value={h.id}>{h.name} · {h.capture_path}</option>
							{/each}
						{:else}
							<option value="">No live inputs</option>
						{/if}
					</select>
					<button
						type="button"
						class="btn btn-small"
						disabled={!liveHosts.length}
						onclick={() => addDeviceFromLive(devicePickId)}
					>
						+ Device
					</button>
				</div>
				<button
					type="button"
					class="btn btn-ghost btn-small"
					onclick={() => addQueueShortcut('spotify')}
				>
					+ Spotify
				</button>
				<button
					type="button"
					class="btn btn-ghost btn-small"
					onclick={() => addQueueShortcut('input')}
				>
					+ Live{liveHosts.length ? ` · ${liveHosts[0].name}` : ''}
				</button>
				<button
					type="button"
					class="btn btn-ghost btn-small"
					onclick={() => addQueueShortcut('youtube')}
				>
					+ YouTube
				</button>
			</div>
			{#if !liveHosts.length}
				<p class="hint muted">
					+ Device needs a live BT A2DP / USB / HDMI / AirPlay host — use Refresh inputs first.
				</p>
			{/if}
			<ul class="queue-list">
				{#each queue as item (item.id)}
					<li class="queue-row queue-row-host" class:active={item.id === activeQueueId}>
						<button type="button" class="queue-main" onclick={() => chooseQueueItem(item.id)}>
							<span class="source-title">
								{item.title}
								{#if item.media_kind === 'video'}
									<span class="type-chip">Video</span>
								{/if}
							</span>
							<span class="source-sub">
								{#if item.input_id}
									{hostName(item.input_id)}
								{:else}
									{item.locator || item.source_id || 'hub'}
								{/if}
								{#if item.id === activeQueueId}
									· active
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
										(t === 'apple' &&
											(streaming?.airplay?.receiving ||
												streaming?.airplay?.configured ||
												liveHosts.some((h) => h.capture_path === 'airplay'))) ||
										(t === 'youtube' &&
											streaming?.youtube?.configured &&
											streaming?.youtube?.playback_mode === 'ytdlp_ffmpeg')
											? ''
											: ' (unavailable)'}</option
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
								<option value="">Hub</option>
								{#each inputs as h}
									<option value={h.id}>{h.name}</option>
								{/each}
							</select>
						</label>
						<span class="status-pill {item.available ? 'linked' : 'missing'}">
							{availabilityLabel(item)}
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
				<h2 id="sources-heading">Catalog</h2>
				<p class="hint">Local audio/video under media/ · live inputs</p>
			</div>
			<div class="catalog-grid">
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
										title={s.available ? 'Playable' : s.note || 'Unavailable — sinks stay up'}
										onclick={() => chooseSource(s.id)}
									>
										<span>
											<span class="source-title">
												{s.title}
												{#if s.media_kind === 'video'}
													<span class="type-chip">Video</span>
												{/if}
											</span>
											{#if s.id === transport?.source_id || s.note}
												<span class="source-sub">
													{s.id === transport?.source_id ? 'active' : ''}{s.id === transport?.source_id &&
													s.note
														? ' · '
														: ''}{s.note || ''}
												</span>
											{/if}
										</span>
										<span class="status-pill {s.available ? 'linked' : 'missing'}">
											{availabilityLabel(s)}
										</span>
									</button>
								</li>
							{:else}
								<li class="empty-group">
									{type === 'local' ? 'Drop files in media/ (wav/flac/mp3/mp4/…)' : 'None'}
								</li>
							{/each}
						</ul>
					</div>
				{/each}
			</div>
		</section>
{/if}

	</div>
{/if}
</main>
</div>
