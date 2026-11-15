/**
 * Thin client for the FastAPI `/api/*` surface.
 * Same-origin in prod (FastAPI serves the built SPA).
 * In `npm run dev`, Vite proxies `/api` → MUSYNC_API_PROXY (default :8080).
 */

/**
 * SPA poll cadence (ms) — cuts idle/hidden API chatter vs prior fixed 1 Hz × 2.
 * Playing: transport 400ms, now-playing 1.5s (snappier Play→UI; client still ticks scrub).
 * Idle: transport 3s, now-playing 6s → ~0.5 req/s.
 * Hidden tab: pause until visible (0 req/s).
 */
export const SPA_POLL_TRANSPORT_PLAYING_MS = 400;
export const SPA_POLL_TRANSPORT_IDLE_MS = 3000;
export const SPA_POLL_NOW_PLAYING_PLAYING_MS = 1500;
export const SPA_POLL_NOW_PLAYING_IDLE_MS = 6000;

/**
 * @param {{ playing?: boolean, hidden?: boolean }} opts
 * @returns {{ transportMs: number, nowPlayingMs: number, paused: boolean }}
 */
export function spaPollCadence({ playing = false, hidden = false } = {}) {
	if (hidden) {
		return { transportMs: 0, nowPlayingMs: 0, paused: true };
	}
	if (playing) {
		return {
			transportMs: SPA_POLL_TRANSPORT_PLAYING_MS,
			nowPlayingMs: SPA_POLL_NOW_PLAYING_PLAYING_MS,
			paused: false
		};
	}
	return {
		transportMs: SPA_POLL_TRANSPORT_IDLE_MS,
		nowPlayingMs: SPA_POLL_NOW_PLAYING_IDLE_MS,
		paused: false
	};
}

/** Steady-state HTTP requests per second for the given cadence (0 when paused). */
export function spaPollRequestsPerSecond(cadence) {
	if (!cadence || cadence.paused) return 0;
	const t = cadence.transportMs > 0 ? 1000 / cadence.transportMs : 0;
	const n = cadence.nowPlayingMs > 0 ? 1000 / cadence.nowPlayingMs : 0;
	return t + n;
}

/** @typedef {'stopped' | 'playing' | 'paused'} TransportState */
/** @typedef {'local' | 'spotify' | 'apple' | 'youtube' | 'input'} SourceType */
/** @typedef {'audio' | 'video'} MediaKind */
/** @typedef {'bt_a2dp' | 'usb' | 'hdmi' | 'airplay' | 'network'} CapturePath */
/** @typedef {'phone' | 'pc' | 'other'} HostKind */

/** @typedef {'bluetooth' | 'network_spp' | 'cast'} DeviceKind */

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
 * @property {string} [cast_uuid]
 * @property {string} [cast_host]
 * @property {number} [cast_port]
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
 * @typedef {Object} DiscoveredCastSink
 * @property {string} name
 * @property {string} [uuid]
 * @property {string} [host]
 * @property {number} [port]
 * @property {string} [model]
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
 * @property {DiscoveredCastSink[]} [cast]
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
 * @property {string} [cast_uuid]
 * @property {string} [cast_host]
 * @property {number} [cast_port]
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
 * @typedef {Object} QueueSettings
 * @property {boolean} loop
 * @property {boolean} turn_taking
 * @property {string | null} [turn_device_id]
 * @property {string[]} [turn_order]
 * @property {string} [rotation_note]
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
 * @property {boolean} [youtube_ready]
 * @property {boolean} [avrcp_supported]
 * @property {boolean} [avrcp_player_registered]
 */

/**
 * @typedef {Object} AvrcpStatus
 * @property {boolean} supported
 * @property {boolean} watching
 * @property {string} [detail]
 * @property {Record<string, number>} [last_volume_sync]
 * @property {string | null} [last_action]
 * @property {string} [last_error]
 * @property {boolean} [player_registered]
 * @property {boolean} [volume_supported]
 * @property {boolean} [dbus_import_ok]
 * @property {string} [player_path]
 * @property {string[]} [registered_adapters]
 * @property {string} [mpris_playback_status]
 */

/**
 * @typedef {Object} StreamingProviderStatus
 * @property {'spotify' | 'youtube'} provider
 * @property {boolean} configured
 * @property {boolean} authenticated
 * @property {string | null} [display_name]
 * @property {boolean | null} [librespot_available]
 * @property {boolean | null} [librespot_running]
 * @property {string | null} [librespot_name]
 * @property {string | null} [librespot_bin]
 * @property {boolean | null} [ytdlp_available]
 * @property {string | null} [ytdlp_version]
 * @property {boolean | null} [ytdlp_outdated]
 * @property {boolean | null} [ffmpeg_available]
 * @property {string | null} [feeder]
 * @property {string | null} [redirect_uri]
 * @property {string | null} [oauth_loopback_uri]
 * @property {string | null} [oauth_hint]
 * @property {boolean | null} [premium_required]
 * @property {string[]} [scopes]
 * @property {string[]} [missing_scopes]
 * @property {string | null} [playback_mode]
 * @property {string | null} [search_market]
 * @property {number | null} [search_limit_max]
 * @property {string[]} [limits]
 * @property {string} [note]
 * @property {string | null} [last_error]
 * @property {string | null} [install_hint]
 * @property {boolean | null} [running]
 */

/**
 * @typedef {Object} AirplayStatus
 * @property {'airplay'} [provider]
 * @property {boolean} configured
 * @property {boolean} [receiving]
 * @property {string | null} [source]
 * @property {string | null} [receiver_name]
 * @property {boolean | null} [shairport_available]
 * @property {string[]} [setup]
 * @property {string} [note]
 */

/**
 * @typedef {Object} SpotifySshTunnelInfo
 * @property {number} port
 * @property {string} host
 * @property {string} user
 * @property {boolean} ready
 * @property {string} command
 * @property {string} open_url
 * @property {string} [script_sh]
 * @property {string} [script_ps1]
 * @property {string[]} [steps]
 * @property {string} [hint]
 */

/**
 * @typedef {Object} SpotifyHttpsTunnelInfo
 * @property {number} port
 * @property {boolean} ready
 * @property {string} command
 * @property {string} [example_redirect_uri]
 * @property {string} [loopback_uri]
 * @property {string} [callback_path]
 * @property {string} [script_sh]
 * @property {string[]} [steps]
 * @property {string} [hint]
 * @property {string} [docs]
 */

/**
 * @typedef {Object} StreamingStatus
 * @property {StreamingProviderStatus} spotify
 * @property {AirplayStatus} airplay
 * @property {StreamingProviderStatus} youtube
 * @property {boolean} [mock_playback]
 * @property {SpotifySshTunnelInfo | null} [ssh_tunnel]
 * @property {SpotifyHttpsTunnelInfo | null} [https_tunnel]
 * @property {string | null} [lan_ip]
 * @property {string | null} [lan_ui_url]
 */

/**
 * @typedef {Object} StreamingSearchHit
 * @property {'spotify' | 'youtube'} provider
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
 * @property {'spotify' | 'youtube'} provider
 * @property {string} query
 * @property {StreamingSearchHit[]} hits
 */

/**
 * @typedef {Object} MaskedCredentialField
 * @property {boolean} set
 * @property {string} [masked]
 * @property {string | null} [value]
 */

/**
 * @typedef {Object} StreamingCredentialsStatus
 * @property {string} env_path
 * @property {boolean} env_exists
 * @property {MaskedCredentialField} spotify_client_id
 * @property {MaskedCredentialField} spotify_client_secret
 * @property {MaskedCredentialField} spotify_redirect_uri
 * @property {MaskedCredentialField} youtube_api_key
 * @property {boolean} spotify_configured
 * @property {boolean} youtube_configured
 * @property {string[]} [updated_keys]
 * @property {boolean} [saved]
 * @property {boolean} [created]
 * @property {string} [lan_ip]
 * @property {string} [lan_ui_url]
 * @property {number} [hub_port]
 * @property {string} [oauth_loopback_uri]
 * @property {boolean} [redirect_uri_allowed]
 * @property {string} [redirect_uri_warning]
 */

/**
 * @typedef {Object} StreamingCredentialsPut
 * @property {string} [spotify_client_id]
 * @property {string} [spotify_client_secret]
 * @property {string} [spotify_redirect_uri]
 * @property {string} [youtube_api_key]
 */

/**
 * @typedef {Object} WifiStatus
 * @property {'mock' | 'nmcli'} backend
 * @property {boolean} connected
 * @property {string | null} [ssid]
 * @property {string[]} ips
 * @property {string | null} [interface]
 * @property {string} [detail]
 * @property {string | null} [mdns_hostname]
 * @property {number} [port]
 * @property {string | null} [lan_url]
 */

/**
 * @typedef {Object} WifiNetwork
 * @property {string} ssid
 * @property {boolean} saved
 * @property {boolean} active
 * @property {boolean} has_password
 */

export const SOURCE_TYPES = /** @type {const} */ (['local', 'spotify', 'apple', 'youtube', 'input']);
export const CAPTURE_PATHS = /** @type {const} */ (['bt_a2dp', 'usb', 'hdmi', 'airplay', 'network']);
export const HOST_KINDS = /** @type {const} */ (['phone', 'pc', 'other']);

/**
 * @param {string} path
 * @param {RequestInit} [options]
 */
/**
 * Prefer FastAPI `{ detail }` (string or validation array) over bare statusText
 * so the UI never only shows "Internal Server Error".
 * @param {unknown} detail
 * @param {string} fallback
 */
function formatApiDetail(detail, fallback) {
	if (typeof detail === 'string' && detail.trim()) return detail.trim();
	if (Array.isArray(detail)) {
		const parts = detail
			.map((item) => {
				if (typeof item === 'string') return item;
				if (item && typeof item === 'object' && 'msg' in item) {
					const loc = Array.isArray(item.loc) ? item.loc.join('.') : '';
					return loc ? `${loc}: ${item.msg}` : String(item.msg);
				}
				return null;
			})
			.filter(Boolean);
		if (parts.length) return parts.join('; ');
	}
	if (detail != null && typeof detail === 'object') {
		try {
			return JSON.stringify(detail);
		} catch {
			/* ignore */
		}
	}
	return fallback;
}

export async function api(path, options = {}) {
	const res = await fetch(path, {
		headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
		...options
	});
	if (!res.ok) {
		const fallback = res.statusText || `HTTP ${res.status}`;
		let detail = fallback;
		try {
			const body = await res.json();
			detail = formatApiDetail(body?.detail ?? body?.error, fallback);
		} catch {
			/* ignore non-JSON error bodies */
		}
		throw new Error(detail);
	}
	if (res.status === 204) return null;
	return res.json();
}

/** @returns {Promise<Health>} */
export function getHealth() {
	return api('/api/health');
}

/** @returns {Promise<AvrcpStatus>} */
export function getAvrcpStatus() {
	return api('/api/avrcp');
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

/** @returns {Promise<QueueSettings>} */
export function getQueueSettings() {
	return api('/api/queue/settings');
}

/**
 * @param {{ loop?: boolean, turn_taking?: boolean }} body
 * @returns {Promise<QueueSettings>}
 */
export function putQueueSettings(body) {
	return api('/api/queue/settings', {
		method: 'PUT',
		body: JSON.stringify(body)
	});
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

/** @returns {Promise<StreamingCredentialsStatus>} */
export function getStreamingCredentials() {
	return api('/api/streaming/credentials');
}

/**
 * Persist Spotify / YouTube keys to ~/musync.env (API returns masked only).
 * Omit or leave blank fields to keep existing values; empty body soft-fails.
 * @param {StreamingCredentialsPut} body
 * @returns {Promise<StreamingCredentialsStatus>}
 */
export function saveStreamingCredentials(body) {
	return api('/api/streaming/credentials', {
		method: 'PUT',
		body: JSON.stringify(body)
	});
}

/**
 * Spotify OAuth start. Honors MUSYNC_SPOTIFY_REDIRECT_URI when it is Spotify-legal
 * (https://… or http://127.0.0.1:…). Never uses http://LAN-IP (Dashboard rejects it).
 */
export const SPOTIFY_LOGIN_HREF = '/api/streaming/spotify/login?loopback=1';

/**
 * Preflight Connect Spotify — returns authorize URL or a clear missing-env error.
 * Defaults to loopback=1; HTTPS env override still wins. Pass `{ loopback: false }`
 * only for request-Host derivation (LAN http is rewritten to 127.0.0.1).
 * @param {{ loopback?: boolean }} [opts]
 * @returns {Promise<{ ok: boolean, url?: string, error?: string, redirect_uri?: string, loopback?: boolean }>}
 */
export function fetchSpotifyAuthorizeUrl(opts = {}) {
	const loopback = opts.loopback !== false;
	return api(`/api/streaming/spotify/authorize-url?loopback=${loopback ? '1' : '0'}`);
}

/** @returns {Promise<StreamingStatus>} */
export function logoutSpotify() {
	return api('/api/streaming/spotify/logout', { method: 'POST', body: '{}' });
}

/**
 * @param {'spotify' | 'youtube'} provider
 * @param {string} q
 * @param {number} [limit]
 * @returns {Promise<StreamingSearchResponse>}
 */
export function searchStreaming(provider, q, limit = 10) {
	// Spotify Dev Mode /search max is 10 — larger values return 400 Invalid limit.
	const n = Math.max(1, Math.min(Number(limit) || 10, 50));
	const params = new URLSearchParams({
		provider,
		q,
		limit: String(Math.trunc(n))
	});
	return api(`/api/streaming/search?${params}`);
}

/**
 * @param {{ provider: 'spotify' | 'youtube', uri: string, title?: string, artist?: string, album?: string, cover_url?: string, duration_ms?: number, activate?: boolean }} body
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
			return 'AirPlay';
		case 'youtube':
			return 'YouTube';
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
		case 'airplay':
			return 'AirPlay receive';
		case 'network':
			return 'Network / upload';
		default:
			return path || '—';
	}
}
