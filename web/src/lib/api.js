/**
 * Thin client for the FastAPI `/api/*` surface.
 * Same-origin in prod (FastAPI serves the built SPA).
 * In `npm run dev`, Vite proxies `/api` → MUSYNC_API_PROXY (default :8080).
 */

/** @typedef {'stopped' | 'playing' | 'paused'} TransportState */

/**
 * @typedef {Object} Device
 * @property {string} id
 * @property {string} name
 * @property {string} [mac]
 * @property {string} [hci]
 * @property {boolean} connected
 * @property {number} delay_ms
 * @property {number} volume
 * @property {boolean} mute
 * @property {boolean} enabled
 */

/**
 * @typedef {Object} Source
 * @property {string} id
 * @property {string} title
 * @property {string} type
 * @property {boolean} available
 * @property {string} [note]
 * @property {number} [duration_ms]
 * @property {string} [path]
 */

/**
 * @typedef {Object} Transport
 * @property {TransportState} state
 * @property {number} position_ms
 * @property {number} duration_ms
 * @property {string | null} [source_id]
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

/**
 * @param {string} id
 * @param {Partial<Pick<Device, 'delay_ms' | 'volume' | 'mute' | 'enabled' | 'name'>>} patch
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
 * @param {{ state?: TransportState, position_ms?: number, source_id?: string }} body
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
