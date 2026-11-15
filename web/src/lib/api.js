/**
 * Thin client for the FastAPI `/api/*` surface.
 * Same-origin in prod (FastAPI serves the built SPA).
 * In `npm run dev`, Vite proxies `/api` → MUSYNC_API_PROXY (default :8080).
 */

/** @typedef {'stopped' | 'playing' | 'paused'} TransportState */
/** @typedef {'local' | 'spotify' | 'apple' | 'input'} SourceType */
/** @typedef {'audio' | 'video'} MediaKind */
/** @typedef {'bt_a2dp' | 'usb' | 'hdmi' | 'network'} CapturePath */
/** @typedef {'phone' | 'pc' | 'other'} HostKind */

/** @typedef {'bluetooth' | 'network_spp'} DeviceKind */

/**
 * @typedef {Object} Device
 * @property {string} id
 * @property {string} name
 * @property {DeviceKind} [kind]
 * @property {string} [mac]
 * @property {string} [hci]
 * @property {boolean} connected
 * @property {number} delay_ms
 * @property {number} volume
 * @property {number} [gain]
 * @property {boolean} mute
 * @property {boolean} enabled
 * @property {string} [listen_host]
 * @property {number} [listen_port]
 * @property {number} [sample_rate]
 * @property {number} [channels]
 */

/**
 * @typedef {Object} VolumeProfile
 * @property {string} id
 * @property {string} name
 * @property {number} preferred_level
 * @property {Record<string, number>} [output_overrides]
 * @property {number} [sensitivity]
 */

/**
 * @typedef {Object} UserDeviceVolume
 * @property {string} user_id
 * @property {string} device_id
 * @property {number} volume
 * @property {string} [updated_at]
 */

/**
 * @typedef {Object} VolumeProfilesState
 * @property {VolumeProfile[]} profiles
 * @property {string | null} active_id
 * @property {Record<string, string>} [assignments]
 * @property {UserDeviceVolume[]} [volumes]
 * @property {Record<string, number>} [device_gains]
 */

/**
 * @typedef {Object} DeviceUserAssignResult
 * @property {VolumeProfilesState} state
 * @property {Device | null} [device]
 * @property {number | null} [suggested_volume]
 * @property {boolean} [applied]
 * @property {string} [detail]
 */

/**
 * @typedef {Object} DiscoveredBluetoothSink
 * @property {string} name
 * @property {string} [mac]
 * @property {string} [pw_node]
 * @property {string} [pw_name]
 * @property {string} [hci]
 * @property {boolean} [paired]
 * @property {boolean} [connected]
 * @property {string | null} [configured_id]
 */

/**
 * @typedef {Object} BluetoothAdapter
 * @property {string} hci
 * @property {string} [mac]
 * @property {string} [name]
 * @property {boolean} [powered]
 * @property {boolean} [healthy]
 * @property {boolean} [onboard]
 * @property {string} [bus]
 * @property {string} [usb_id]
 * @property {string} [detail]
 */

/**
 * @typedef {Object} DeviceDiscoverResponse
 * @property {DiscoveredBluetoothSink[]} bluetooth
 * @property {Device} spp_default
 * @property {BluetoothAdapter[]} [adapters]
 * @property {number} [adapter_capacity]
 * @property {string} [soft_warn]
 * @property {string} [config_path]
 * @property {boolean} [config_ok]
 * @property {string} [config_detail]
 * @property {string} [detail]
 */

/**
 * @typedef {Object} BluetoothScanStatus
 * @property {boolean} scanning
 * @property {DiscoveredBluetoothSink[]} bluetooth
 * @property {BluetoothAdapter[]} [adapters]
 * @property {number} [adapter_capacity]
 * @property {string} [soft_warn]
 * @property {string} [detail]
 */

/**
 * @typedef {Object} BluetoothConnectResponse
 * @property {boolean} ok
 * @property {string} mac
 * @property {string} [detail]
 * @property {Device | null} [device]
 * @property {DiscoveredBluetoothSink[]} [bluetooth]
 * @property {BluetoothAdapter[]} [adapters]
 * @property {string} [soft_warn]
 */

/**
 * @typedef {Object} DeviceConfigEntry
 * @property {string} [id]
 * @property {string} name
 * @property {DeviceKind} kind
 * @property {boolean} [enabled]
 * @property {string} [mac]
 * @property {string} [hci]
 * @property {string} [pw_node]
 * @property {number} [delay_ms]
 * @property {number} [volume]
 * @property {boolean} [mute]
 * @property {string} [listen_host]
 * @property {number} [listen_port]
 * @property {number} [sample_rate]
 * @property {number} [channels]
 */

/**
 * @typedef {Object} Source
 * @property {string} id
 * @property {string} title
 * @property {SourceType} type
 * @property {boolean} available
 * @property {string} [note]
 * @property {number} [duration_ms]
 * @property {string} [path]
 * @property {string} [locator]
 * @property {string | null} [input_id]
 * @property {MediaKind} [media_kind]
 */

/**
 * @typedef {Object} InputHost
 * @property {string} id
 * @property {string} name
 * @property {HostKind} kind
 * @property {CapturePath} capture_path
 * @property {boolean} armed
 * @property {boolean} connected
 * @property {string | null} [endpoint]
 * @property {boolean} offers_local
 * @property {boolean} offers_spotify
 * @property {boolean} offers_apple
 * @property {boolean} offers_live_input
 * @property {string | null} [note]
 */

/**
 * @typedef {Object} DiscoveredCaptureSource
 * @property {string} name
 * @property {string} [pw_node]
 * @property {string} [pw_name]
 * @property {string} [mac]
 * @property {CapturePath} [capture_path]
 * @property {boolean} [connected]
 * @property {string | null} [configured_id]
 */

/**
 * @typedef {Object} CaptureDiscoverResponse
 * @property {DiscoveredCaptureSource[]} sources
 * @property {string} [detail]
 */

/**
 * @typedef {Object} QueueItem
 * @property {string} id
 * @property {string} title
 * @property {SourceType} source_type
 * @property {string | null} [locator]
 * @property {string | null} [source_id]
 * @property {string | null} [path]
 * @property {string | null} [input_id]
 * @property {boolean} available
 * @property {string | null} [note]
 * @property {number | null} [duration_ms]
 * @property {MediaKind} [media_kind]
 * @property {string | null} [artist]
 * @property {string | null} [album]
 * @property {string | null} [cover_url]
 */

/**
 * @typedef {Object} Transport
 * @property {TransportState} state
 * @property {number} position_ms
 * @property {number} duration_ms
 * @property {string | null} [source_id]
 * @property {string | null} [queue_item_id]
 * @property {SourceType | null} [source_type]
 * @property {string | null} [input_id]
 * @property {string | null} [title]
 * @property {string | null} [artist]
 * @property {string | null} [album]
 * @property {string | null} [cover_url]
 * @property {MediaKind | null} [media_kind]
 */

/**
 * @typedef {Object} NowPlaying
 * @property {string | null} [title]
 * @property {string | null} [artist]
 * @property {string | null} [album]
 * @property {string | null} [cover_url]
 * @property {boolean} [has_cover]
 * @property {SourceType | null} [source_type]
 * @property {string | null} [input_id]
 * @property {string | null} [queue_item_id]
 * @property {string | null} [locator]
 * @property {TransportState} [state]
 * @property {number} [position_ms]
 * @property {number} [duration_ms]
 * @property {string | null} [note]
 */

/**
 * @typedef {Object} Health
 * @property {boolean} ok
 * @property {'mock' | 'pipewire'} backend
 * @property {boolean} pipewire_up
 * @property {number} sink_count
 * @property {number} enabled_count
 * @property {Record<string, string>} [hci_map]
 * @property {BluetoothAdapter[]} [adapters]
 * @property {number} [adapter_count]
 * @property {number} [healthy_adapter_count]
 * @property {string} [detail]
 * @property {boolean} [spotify_ready]
 * @property {boolean} [apple_ready]
 */

/**
 * @typedef {Object} StreamingProviderStatus
 * @property {'spotify' | 'apple'} provider
 * @property {boolean} configured
 * @property {boolean} authenticated
 * @property {string | null} [display_name]
 * @property {boolean | null} [librespot_available]
 * @property {boolean | null} [librespot_running]
 * @property {string | null} [librespot_name]
 * @property {string | null} [feeder]
 * @property {string | null} [redirect_uri]
 * @property {boolean | null} [premium_required]
 * @property {boolean | null} [user_token_set]
 * @property {string | null} [airplay_source]
 * @property {string | null} [playback_mode]
 * @property {string[]} [limits]
 * @property {string} [note]
 */

/**
 * @typedef {Object} StreamingStatus
 * @property {StreamingProviderStatus} spotify
 * @property {StreamingProviderStatus} apple
 * @property {boolean} [mock_playback]
 */

/**
 * @typedef {Object} StreamingSearchHit
 * @property {'spotify' | 'apple'} provider
 * @property {string} id
 * @property {string} uri
 * @property {string} title
 * @property {string} artist
 * @property {number | null} [duration_ms]
 * @property {string | null} [album]
 * @property {string | null} [cover_url]
 */

/**
 * @typedef {Object} StreamingSearchResponse
 * @property {'spotify' | 'apple'} provider
 * @property {string} query
 * @property {StreamingSearchHit[]} hits
 */

/**
 * @typedef {Object} WifiStatus
 * @property {'mock' | 'nmcli'} backend
 * @property {boolean} connected
 * @property {string | null} [ssid]
 * @property {string[]} ips
 * @property {string | null} [interface]
 * @property {string} [detail]
 */

/**
 * @typedef {Object} WifiNetwork
 * @property {string} ssid
 * @property {boolean} saved
 * @property {boolean} active
 * @property {boolean} has_password
 */

export const SOURCE_TYPES = /** @type {const} */ (['local', 'spotify', 'apple', 'input']);
export const CAPTURE_PATHS = /** @type {const} */ (['bt_a2dp', 'usb', 'hdmi', 'network']);
export const HOST_KINDS = /** @type {const} */ (['phone', 'pc', 'other']);

/**
 * @param {string} path
 * @param {RequestInit} [options]
 */
export async function api(path, options = {}) {
	const res = await fetch(path, {
		headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
		...options
	});
	if (!res.ok) {
		let detail = res.statusText;
		try {
			const body = await res.json();
			detail = body.detail || detail;
		} catch {
			/* ignore */
		}
		throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
	}
	if (res.status === 204) return null;
	return res.json();
}

/** @returns {Promise<Health>} */
export function getHealth() {
	return api('/api/health');
}

/** @returns {Promise<Device[]>} */
export function listDevices() {
	return api('/api/devices');
}

/** @returns {Promise<DeviceDiscoverResponse>} */
export function discoverDevices() {
	return api('/api/devices/discover');
}

/** @returns {Promise<BluetoothScanStatus>} */
export function getBluetoothScan() {
	return api('/api/bluetooth/scan');
}

/** @returns {Promise<BluetoothScanStatus>} */
export function startBluetoothScan() {
	return api('/api/bluetooth/scan', { method: 'POST' });
}

/** @returns {Promise<BluetoothScanStatus>} */
export function stopBluetoothScan() {
	return api('/api/bluetooth/scan', { method: 'DELETE' });
}

/** @returns {Promise<BluetoothScanStatus>} */
export function clearBluetoothDiscovery() {
	return api('/api/bluetooth/discover/clear', { method: 'POST' });
}

/**
 * @param {{ mac: string, name?: string, hci?: string, enable_as_output?: boolean }} body
 * @returns {Promise<BluetoothConnectResponse>}
 */
export function connectBluetooth(body) {
	return api('/api/bluetooth/connect', {
		method: 'POST',
		body: JSON.stringify(body)
	});
}

/**
 * @param {DeviceConfigEntry[]} devices
 * @returns {Promise<Device[]>}
 */
export function putDevicesConfig(devices) {
	return api('/api/devices/config', {
		method: 'PUT',
		body: JSON.stringify({ devices })
	});
}

/**
 * Per-output delay/volume/gain/mute/enabled — same PATCH surface MDAS-24 fixes on the Pi.
 * @param {string} id
 * @param {Partial<Pick<Device, 'delay_ms' | 'volume' | 'gain' | 'mute' | 'enabled' | 'name' | 'kind' | 'listen_host' | 'listen_port' | 'sample_rate' | 'channels'>>} patch
 * @returns {Promise<Device>}
 */
export function patchDevice(id, patch) {
	return api(`/api/devices/${encodeURIComponent(id)}`, {
		method: 'PATCH',
		body: JSON.stringify(patch)
	});
}

/** @returns {Promise<VolumeProfilesState>} */
export function listProfiles() {
	return api('/api/profiles');
}

/**
 * @param {{ name: string, preferred_level?: number, output_overrides?: Record<string, number> }} body
 * @returns {Promise<VolumeProfile>}
 */
export function createProfile(body) {
	return api('/api/profiles', {
		method: 'POST',
		body: JSON.stringify(body)
	});
}

/**
 * @param {string} id
 * @param {{ name?: string, preferred_level?: number, output_overrides?: Record<string, number> }} patch
 * @returns {Promise<VolumeProfile>}
 */
export function patchProfile(id, patch) {
	return api(`/api/profiles/${encodeURIComponent(id)}`, {
		method: 'PATCH',
		body: JSON.stringify(patch)
	});
}

/**
 * @param {string} id
 * @returns {Promise<null>}
 */
export function deleteProfile(id) {
	return api(`/api/profiles/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

/**
 * Select a profile (null clears) and apply preferred × gain volumes to outputs.
 * @param {string | null} id
 * @returns {Promise<VolumeProfilesState>}
 */
export function setActiveProfile(id) {
	return api('/api/profiles/active', {
		method: 'PUT',
		body: JSON.stringify({ id })
	});
}

/**
 * Apply a profile to all outputs without changing the active selection.
 * @param {string} id
 * @returns {Promise<Device[]>}
 */
export function applyProfile(id) {
	return api(`/api/profiles/${encodeURIComponent(id)}/apply`, {
		method: 'POST',
		body: '{}'
	});
}

/**
 * Assign (or clear) which listening profile/user owns an output device.
 * Presets volume via learned S_u × G_d when possible.
 * @param {{ device_id: string, profile_id: string | null, apply_suggested?: boolean }} body
 * @returns {Promise<DeviceUserAssignResult>}
 */
export function assignDeviceUser(body) {
	return api('/api/profiles/assignments', {
		method: 'PUT',
		body: JSON.stringify(body)
	});
}

/** @returns {Promise<Transport>} */
export function getTransport() {
	return api('/api/transport');
}

/**
 * @param {{ state?: TransportState, position_ms?: number, source_id?: string, queue_item_id?: string }} body
 * @returns {Promise<Transport>}
 */
export function putTransport(body) {
	return api('/api/transport', {
		method: 'PUT',
		body: JSON.stringify(body)
	});
}

/** @returns {Promise<Source[]>} */
export function listSources() {
	return api('/api/sources');
}

/**
 * @param {string} id
 * @returns {Promise<Transport>}
 */
export function setActiveSource(id) {
	return api('/api/sources/active', {
		method: 'PUT',
		body: JSON.stringify({ id })
	});
}

/** @returns {Promise<QueueItem[]>} */
export function listQueue() {
	return api('/api/queue');
}

/**
 * @param {{ title?: string, source_type?: SourceType, locator?: string, source_id?: string, path?: string, input_id?: string }} body
 * @returns {Promise<QueueItem>}
 */
export function addQueueItem(body) {
	return api('/api/queue', {
		method: 'POST',
		body: JSON.stringify(body)
	});
}

/**
 * @param {string} id
 * @param {{ title?: string, source_type?: SourceType, locator?: string, source_id?: string, path?: string, input_id?: string | null }} patch
 * @returns {Promise<QueueItem>}
 */
export function patchQueueItem(id, patch) {
	return api(`/api/queue/${encodeURIComponent(id)}`, {
		method: 'PATCH',
		body: JSON.stringify(patch)
	});
}

/** @returns {Promise<InputHost[]>} */
export function listInputs() {
	return api('/api/inputs');
}

/** @returns {Promise<CaptureDiscoverResponse>} */
export function discoverInputs() {
	return api('/api/inputs/discover');
}

/**
 * @param {{ name: string, kind?: HostKind, capture_path?: CapturePath, armed?: boolean, connected?: boolean, endpoint?: string }} body
 * @returns {Promise<InputHost>}
 */
export function addInput(body) {
	return api('/api/inputs', {
		method: 'POST',
		body: JSON.stringify(body)
	});
}

/**
 * @param {string} id
 * @param {Partial<InputHost>} patch
 * @returns {Promise<InputHost>}
 */
export function patchInput(id, patch) {
	return api(`/api/inputs/${encodeURIComponent(id)}`, {
		method: 'PATCH',
		body: JSON.stringify(patch)
	});
}

/**
 * @param {string} id
 * @param {File} file
 * @returns {Promise<InputHost>}
 */
export async function uploadInputFile(id, file) {
	const body = new FormData();
	body.append('file', file);
	const res = await fetch(`/api/inputs/${encodeURIComponent(id)}/upload`, {
		method: 'POST',
		body
	});
	if (!res.ok) {
		let detail = res.statusText;
		try {
			const json = await res.json();
			detail = json.detail || detail;
		} catch {
			/* ignore */
		}
		throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
	}
	return res.json();
}

/**
 * @param {string} id
 * @returns {Promise<null>}
 */
export function deleteInput(id) {
	return api(`/api/inputs/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

/**
 * @param {string} id
 * @returns {Promise<null>}
 */
export function deleteQueueItem(id) {
	return api(`/api/queue/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

/**
 * @param {string} id
 * @returns {Promise<Transport>}
 */
export function setActiveQueueItem(id) {
	return api('/api/queue/active', {
		method: 'PUT',
		body: JSON.stringify({ id })
	});
}


/** @returns {Promise<StreamingStatus>} */
export function getStreamingStatus() {
	return api('/api/streaming');
}

/** Spotify OAuth start — same-origin link redirect (not a password form). */
export const SPOTIFY_LOGIN_HREF = '/api/streaming/spotify/login';

/**
 * Preflight Connect Spotify — returns authorize URL or a clear missing-env error.
 * @returns {Promise<{ ok: boolean, url?: string, error?: string, redirect_uri?: string }>}
 */
export function fetchSpotifyAuthorizeUrl() {
	return api('/api/streaming/spotify/authorize-url');
}

/** @returns {Promise<StreamingStatus>} */
export function logoutSpotify() {
	return api('/api/streaming/spotify/logout', { method: 'POST', body: '{}' });
}

/**
 * @param {'spotify' | 'apple'} provider
 * @param {string} q
 * @param {number} [limit]
 * @returns {Promise<StreamingSearchResponse>}
 */
export function searchStreaming(provider, q, limit = 15) {
	const params = new URLSearchParams({
		provider,
		q,
		limit: String(limit)
	});
	return api(`/api/streaming/search?${params}`);
}

/**
 * @param {{ provider: 'spotify' | 'apple', uri: string, title?: string, artist?: string, album?: string, cover_url?: string, duration_ms?: number, activate?: boolean }} body
 * @returns {Promise<QueueItem>}
 */
export function addStreamingTrack(body) {
	return api('/api/streaming/queue', {
		method: 'POST',
		body: JSON.stringify(body)
	});
}

/** @returns {Promise<NowPlaying>} */
export function getNowPlaying() {
	return api('/api/now-playing');
}

/** @returns {Promise<WifiStatus>} */
export function getWifiStatus() {
	return api('/api/wifi');
}

/** @returns {Promise<WifiNetwork[]>} */
export function listWifiNetworks() {
	return api('/api/wifi/networks');
}

/**
 * @param {{ ssid: string, password?: string }} body
 * @returns {Promise<WifiNetwork>}
 */
export function saveWifiNetwork(body) {
	return api('/api/wifi/networks', {
		method: 'POST',
		body: JSON.stringify(body)
	});
}

/**
 * @param {{ ssid: string, password?: string }} body
 * @returns {Promise<WifiStatus>}
 */
export function connectWifi(body) {
	return api('/api/wifi/connect', {
		method: 'POST',
		body: JSON.stringify(body)
	});
}

/**
 * @param {string} ssid
 * @returns {Promise<null>}
 */
export function forgetWifiNetwork(ssid) {
	return api(`/api/wifi/networks/${encodeURIComponent(ssid)}`, {
		method: 'DELETE'
	});
}

/** @param {number} ms */
export function fmtMs(ms) {
	const total = Math.max(0, Math.floor((ms || 0) / 1000));
	const m = Math.floor(total / 60);
	const s = total % 60;
	return `${m}:${String(s).padStart(2, '0')}`;
}

/** @param {SourceType | string | null | undefined} type */
export function sourceTypeLabel(type) {
	switch (type) {
		case 'local':
			return 'Local';
		case 'spotify':
			return 'Spotify';
		case 'apple':
			return 'Apple Music';
		case 'input':
			return 'Live input';
		default:
			return type || '—';
	}
}

/**
 * Relative media URL for optional muted video preview (audio still via feeder).
 * @param {string | null | undefined} sourceId catalog id like `local:clip.mp4`
 */
export function mediaPreviewUrl(sourceId) {
	if (!sourceId || !sourceId.startsWith('local:')) return null;
	const rel = sourceId.slice('local:'.length);
	if (!rel || rel.startsWith('demo/')) return null;
	return `/api/media/${rel.split('/').map(encodeURIComponent).join('/')}`;
}

/** @param {CapturePath | string | null | undefined} path */
export function capturePathLabel(path) {
	switch (path) {
		case 'bt_a2dp':
			return 'BT A2DP source';
		case 'usb':
			return 'USB audio';
		case 'hdmi':
			return 'HDMI / capture';
		case 'network':
			return 'Network / upload';
		default:
			return path || '—';
	}
}
