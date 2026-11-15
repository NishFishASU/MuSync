/**
 * Thin client for the FastAPI `/api/*` surface.
 * Same-origin in prod (FastAPI serves the built SPA).
 * In `npm run dev`, Vite proxies `/api` → MUSYNC_API_PROXY (default :8080).
 */

/** @typedef {'stopped' | 'playing' | 'paused'} TransportState */
/** @typedef {'local' | 'spotify' | 'apple' | 'input'} SourceType */

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
 * @property {SourceType} type
 * @property {boolean} available
 * @property {string} [note]
 * @property {number} [duration_ms]
 * @property {string} [path]
 * @property {string} [locator]
 */

/**
 * @typedef {Object} QueueItem
 * @property {string} id
 * @property {string} title
 * @property {SourceType} source_type
 * @property {string | null} [locator]
 * @property {string | null} [source_id]
 * @property {string | null} [path]
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

export const SOURCE_TYPES = /** @type {const} */ (['local', 'spotify', 'apple', 'input']);

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
 * @param {{ title?: string, source_type?: SourceType, locator?: string, source_id?: string, path?: string }} body
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
 * @param {{ title?: string, source_type?: SourceType, locator?: string, source_id?: string, path?: string }} patch
 * @returns {Promise<QueueItem>}
 */
export function patchQueueItem(id, patch) {
	return api(`/api/queue/${encodeURIComponent(id)}`, {
		method: 'PATCH',
		body: JSON.stringify(patch)
	});
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
			return 'Input';
		default:
			return type || '—';
	}
}
