/**
 * Thin client for the FastAPI `/api/*` surface.
 * Same-origin in prod (FastAPI serves the built SPA).
 * In `npm run dev`, Vite proxies `/api` → MUSYNC_API_PROXY (default :8080).
 */

/** @typedef {'stopped' | 'playing' | 'paused'} TransportState */
/** @typedef {'local' | 'spotify' | 'apple' | 'input'} SourceType */
/** @typedef {'bt_a2dp' | 'usb' | 'network'} CapturePath */
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
 * @property {boolean} mute
 * @property {boolean} enabled
 * @property {string} [listen_host]
 * @property {number} [listen_port]
 * @property {number} [sample_rate]
 * @property {number} [channels]
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
 * @typedef {Object} DeviceDiscoverResponse
 * @property {DiscoveredBluetoothSink[]} bluetooth
 * @property {Device} spp_default
 * @property {string} [config_path]
 * @property {boolean} [config_ok]
 * @property {string} [config_detail]
 * @property {string} [detail]
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
 */

/**
 * @typedef {Object} Health
 * @property {boolean} ok
 * @property {'mock' | 'pipewire'} backend
 * @property {boolean} pipewire_up
 * @property {number} sink_count
 * @property {number} enabled_count
 * @property {Record<string, string>} [hci_map]
 * @property {string} [detail]
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
export const CAPTURE_PATHS = /** @type {const} */ (['bt_a2dp', 'usb', 'network']);
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
 * @param {string} id
 * @param {Partial<Pick<Device, 'delay_ms' | 'volume' | 'mute' | 'enabled' | 'name' | 'kind' | 'listen_host' | 'listen_port' | 'sample_rate' | 'channels'>>} patch
 * @returns {Promise<Device>}
 */
export function patchDevice(id, patch) {
	return api(`/api/devices/${encodeURIComponent(id)}`, {
		method: 'PATCH',
		body: JSON.stringify(patch)
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

/** @param {CapturePath | string | null | undefined} path */
export function capturePathLabel(path) {
	switch (path) {
		case 'bt_a2dp':
			return 'BT A2DP source';
		case 'usb':
			return 'USB audio';
		case 'network':
			return 'Network / upload';
		default:
			return path || '—';
	}
}
