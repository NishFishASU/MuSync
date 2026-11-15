"""PipeWire / WirePlumber / Bluetooth CLI helpers (safe without hardware).

These helpers parse tool output and build shell command lists. Callers decide
whether to execute (Pi with tools) or only log/plan (cloud dry-run).
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

# bluez_output.XX_XX_XX_XX_XX_XX or bluez_sink.XX_XX_...
_BLUEZ_MAC_RE = re.compile(
    r"bluez[_-](?:output|sink)\.([0-9A-Fa-f]{2}(?:_[0-9A-Fa-f]{2}){5})",
    re.IGNORECASE,
)
# Phone/PC as A2DP source → Pi: bluez_input.XX_XX_… or bluez_source.…
_BLUEZ_INPUT_MAC_RE = re.compile(
    r"bluez[_-](?:input|source)\.([0-9A-Fa-f]{2}(?:_[0-9A-Fa-f]{2}){5})",
    re.IGNORECASE,
)
_MAC_COLON_RE = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")


def which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def run_cmd(
    args: list[str],
    *,
    timeout: float = 5.0,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )


def normalize_mac(mac: str) -> str:
    """AA:BB:CC:DD:EE:01 → uppercase colon form."""
    cleaned = mac.strip().replace("-", ":").replace("_", ":").upper()
    parts = [p for p in cleaned.split(":") if p]
    if len(parts) != 6:
        return cleaned
    return ":".join(p.zfill(2) for p in parts)


def mac_to_bluez_suffix(mac: str) -> str:
    """AA:BB:… → AA_BB_… (PipeWire bluez node naming)."""
    return normalize_mac(mac).replace(":", "_")


@dataclass
class PwNode:
    """A discovered PipeWire / WirePlumber sink-ish node."""

    id: str
    name: str
    description: str = ""
    mac: str = ""
    media_class: str = ""


@dataclass
class GraphLink:
    """One planned link in the fan-out graph."""

    device_id: str
    source: str
    target: str
    delay_ms: int
    volume: float
    mute: bool
    commands: list[list[str]] = field(default_factory=list)


@dataclass
class GraphPlan:
    """Feeder → per-sink delay/volume → bluez sinks."""

    feeder: str
    links: list[GraphLink] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    dry_run: bool = True

    def as_shell_script(self) -> str:
        lines = [
            "#!/usr/bin/env bash",
            "# Generated MuSync PipeWire graph plan — review before running on Pi.",
            "set -euo pipefail",
            f"# feeder: {self.feeder}",
            "",
        ]
        for note in self.notes:
            lines.append(f"# note: {note}")
        if self.notes:
            lines.append("")
        for link in self.links:
            lines.append(
                f"# {link.device_id}: delay={link.delay_ms}ms vol={link.volume} mute={link.mute}"
            )
            lines.append(f"#   {link.source} → {link.target}")
            for cmd in link.commands:
                lines.append(" ".join(sh_quote(a) for a in cmd))
            lines.append("")
        for skip in self.skipped:
            lines.append(f"# skipped: {skip}")
        return "\n".join(lines).rstrip() + "\n"


def sh_quote(arg: str) -> str:
    if re.fullmatch(r"[-A-Za-z0-9_./:=+@%]+", arg):
        return arg
    return "'" + arg.replace("'", "'\"'\"'") + "'"


_WPCTL_NODE_LINE = re.compile(
    r"^\s*[│|]?\s*\*?\s*(\d+)\.\s+(\S+)(?:\s+(.*))?$"
)


def _parse_wpctl_section(text: str, section: str, media_class: str) -> list[PwNode]:
    """Extract nodes from a wpctl status section (Sinks / Sources)."""
    nodes: list[PwNode] = []
    in_section = False
    section_key = f"{section}:"
    for raw in text.splitlines():
        line = raw.rstrip()
        lower = line.lower()
        if section_key in lower:
            in_section = True
            continue
        if in_section:
            # Next major Audio subsection ends this one.
            if re.search(
                r"^\s*[├└│|].*(sinks|sources|filters|streams|devices):",
                lower,
            ) or re.match(r"^\s*(sinks|sources|filters|streams|devices):", lower):
                if section_key not in lower:
                    in_section = False
                    continue
        if not in_section:
            continue
        m = _WPCTL_NODE_LINE.search(line)
        if not m:
            continue
        node_id, name, rest = m.group(1), m.group(2), (m.group(3) or "").strip()
        name = name.rstrip(".")
        mac = ""
        for pattern in (_BLUEZ_MAC_RE, _BLUEZ_INPUT_MAC_RE):
            bm = pattern.search(name) or pattern.search(rest)
            if bm:
                mac = normalize_mac(bm.group(1))
                break
        nodes.append(
            PwNode(
                id=node_id,
                name=name,
                description=rest,
                mac=mac,
                media_class=media_class,
            )
        )
    return nodes


def parse_wpctl_status(text: str) -> list[PwNode]:
    """Extract sink-like entries from `wpctl status` (best-effort)."""
    return _parse_wpctl_section(text, "sinks", "Audio/Sink")


def parse_wpctl_sources(text: str) -> list[PwNode]:
    """Extract source-like entries (bluez_input / USB mic) from `wpctl status`."""
    return _parse_wpctl_section(text, "sources", "Audio/Source")


def parse_pw_cli_list_nodes(text: str) -> list[PwNode]:
    """Parse `pw-cli list-objects Node` / `pw-cli ls Node` style dumps."""
    nodes: list[PwNode] = []
    current: dict[str, str] = {}

    def flush() -> None:
        nonlocal current
        if not current:
            return
        node_id = current.get("id") or current.get("object.serial") or ""
        name = (
            current.get("node.name")
            or current.get("name")
            or current.get("application.name")
            or ""
        )
        if node_id and name:
            mac = ""
            bm = _BLUEZ_MAC_RE.search(name)
            if bm:
                mac = normalize_mac(bm.group(1))
            if not mac:
                cm = _MAC_COLON_RE.search(current.get("device.string", ""))
                if cm:
                    mac = normalize_mac(cm.group(1))
            nodes.append(
                PwNode(
                    id=str(node_id),
                    name=name,
                    description=current.get("node.description", ""),
                    mac=mac,
                    media_class=current.get("media.class", ""),
                )
            )
        current = {}

    id_re = re.compile(r"id\s+(\d+)", re.IGNORECASE)
    kv_re = re.compile(r"^\s*([A-Za-z0-9._-]+)\s*=\s*\"?([^\"]*)\"?\s*$")
    for raw in text.splitlines():
        line = raw.rstrip()
        id_m = id_re.search(line)
        if id_m and ("type" in line.lower() or line.strip().startswith("id")):
            flush()
            current = {"id": id_m.group(1)}
            continue
        kv = kv_re.match(line)
        if kv and current is not None:
            current[kv.group(1)] = kv.group(2)
    flush()
    # Prefer Audio/Sink nodes when media.class is known.
    sinks = [n for n in nodes if "sink" in n.media_class.lower()]
    return sinks or nodes


def discover_nodes(
    *,
    wpctl: Optional[str],
    pw_cli: Optional[str],
) -> tuple[list[PwNode], str]:
    """Discover sink nodes; return (nodes, detail)."""
    if wpctl:
        try:
            proc = run_cmd([wpctl, "status"], timeout=4.0)
            if proc.returncode == 0 and proc.stdout.strip():
                nodes = parse_wpctl_status(proc.stdout)
                return nodes, f"wpctl status → {len(nodes)} sink entries"
            detail = (proc.stderr or proc.stdout or "wpctl status failed").strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            detail = f"wpctl status error: {exc}"
    else:
        detail = "wpctl missing"

    if pw_cli:
        try:
            proc = run_cmd([pw_cli, "ls", "Node"], timeout=4.0)
            if proc.returncode != 0:
                proc = run_cmd([pw_cli, "list-objects", "Node"], timeout=4.0)
            if proc.returncode == 0 and proc.stdout.strip():
                nodes = parse_pw_cli_list_nodes(proc.stdout)
                return nodes, f"pw-cli → {len(nodes)} nodes ({detail})"
            detail = (
                f"{detail}; pw-cli failed: "
                f"{(proc.stderr or proc.stdout or '').strip()}"
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            detail = f"{detail}; pw-cli error: {exc}"

    return [], detail


def parse_pw_cli_list_sources(text: str) -> list[PwNode]:
    """Like parse_pw_cli_list_nodes but prefer Audio/Source."""
    nodes: list[PwNode] = []
    current: dict[str, str] = {}

    def flush() -> None:
        nonlocal current
        if not current:
            return
        node_id = current.get("id") or current.get("object.serial") or ""
        name = (
            current.get("node.name")
            or current.get("name")
            or current.get("application.name")
            or ""
        )
        if node_id and name:
            mac = ""
            for pattern in (_BLUEZ_INPUT_MAC_RE, _BLUEZ_MAC_RE):
                bm = pattern.search(name)
                if bm:
                    mac = normalize_mac(bm.group(1))
                    break
            if not mac:
                cm = _MAC_COLON_RE.search(current.get("device.string", ""))
                if cm:
                    mac = normalize_mac(cm.group(1))
            nodes.append(
                PwNode(
                    id=str(node_id),
                    name=name,
                    description=current.get("node.description", ""),
                    mac=mac,
                    media_class=current.get("media.class", ""),
                )
            )
        current = {}

    id_re = re.compile(r"id\s+(\d+)", re.IGNORECASE)
    kv_re = re.compile(r"^\s*([A-Za-z0-9._-]+)\s*=\s*\"?([^\"]*)\"?\s*$")
    for raw in text.splitlines():
        line = raw.rstrip()
        id_m = id_re.search(line)
        if id_m and ("type" in line.lower() or line.strip().startswith("id")):
            flush()
            current = {"id": id_m.group(1)}
            continue
        kv = kv_re.match(line)
        if kv and current is not None:
            current[kv.group(1)] = kv.group(2)
    flush()
    sources = [n for n in nodes if "source" in n.media_class.lower()]
    return sources or nodes


def discover_sources(
    *,
    wpctl: Optional[str],
    pw_cli: Optional[str],
) -> tuple[list[PwNode], str]:
    """Discover Audio/Source nodes (bluez_input, USB capture, etc.)."""
    if wpctl:
        try:
            proc = run_cmd([wpctl, "status"], timeout=4.0)
            if proc.returncode == 0 and proc.stdout.strip():
                nodes = parse_wpctl_sources(proc.stdout)
                return nodes, f"wpctl sources → {len(nodes)} entries"
            detail = (proc.stderr or proc.stdout or "wpctl status failed").strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            detail = f"wpctl status error: {exc}"
    else:
        detail = "wpctl missing"

    if pw_cli:
        try:
            proc = run_cmd([pw_cli, "ls", "Node"], timeout=4.0)
            if proc.returncode != 0:
                proc = run_cmd([pw_cli, "list-objects", "Node"], timeout=4.0)
            if proc.returncode == 0 and proc.stdout.strip():
                nodes = parse_pw_cli_list_sources(proc.stdout)
                return nodes, f"pw-cli sources → {len(nodes)} ({detail})"
            detail = (
                f"{detail}; pw-cli failed: "
                f"{(proc.stderr or proc.stdout or '').strip()}"
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            detail = f"{detail}; pw-cli error: {exc}"

    return [], detail


# HDMI / capture-card name fragments (USB HDMI dongles often look like UAC).
_HDMI_CAPTURE_MARKERS = (
    "hdmi",
    "capture",
    "ms2109",
    "ms2130",
    "ms210x",
    "macrosilicon",
    "live gamer",
    "elgato",
    "avermedia",
    "magewell",
    "hdmi2usb",
    "hd video",
    "video capture",
    "usb3h",
)


def looks_like_hdmi_capture(text: str) -> bool:
    """True when ALSA/PipeWire labels describe an HDMI or capture-card source."""
    hay = (text or "").lower()
    if not hay:
        return False
    return any(m in hay for m in _HDMI_CAPTURE_MARKERS)


def classify_capture_source(node: PwNode) -> str:
    """Return capture_path value: bt_a2dp | hdmi | usb | network (fallback)."""
    hay = f"{node.name} {node.description}".lower()
    if "bluez_input" in hay or "bluez_source" in hay or (
        node.mac and "bluez" in hay
    ):
        return "bt_a2dp"
    # HDMI / capture cards before generic USB — many are USB UAC dongles.
    if looks_like_hdmi_capture(hay) or node.id.startswith("alsa:hdmi:"):
        return "hdmi"
    # Prefer real USB sound cards / UAC — not bare onboard "analog" mics.
    if (
        "alsa_input.usb" in hay
        or "usb-audio" in hay
        or "usb audio" in hay
        or ("usb" in hay and ("alsa_input" in hay or "input" in hay))
        or hay.startswith("hw:")
        or node.id.startswith("alsa:usb:")
        or (node.id.startswith("alsa:") and not node.id.startswith("alsa:hdmi:"))
    ):
        return "usb"
    if "alsa_input" in hay and "usb" in hay:
        return "usb"
    return "network"


def is_capture_candidate(node: PwNode) -> bool:
    """Filter monitor/virtual sources; keep BT / USB / HDMI capture inputs."""
    hay = f"{node.name} {node.description}".lower()
    if "musync_feeder" in hay or ".monitor" in hay:
        return False
    if "bluez_input" in hay or "bluez_source" in hay:
        return True
    if looks_like_hdmi_capture(hay) or node.id.startswith("alsa:hdmi:"):
        return True
    if "alsa_input.usb" in hay or ("usb" in hay and "alsa_input" in hay):
        return True
    if "usb" in hay and "input" in hay and "monitor" not in hay:
        return True
    # Named phone remotes sometimes show without bluez_ prefix in description.
    if node.mac and "bluez" in hay:
        return True
    return False


@dataclass
class AlsaUsbCard:
    """A USB sound card seen by ALSA (capture-capable preferred)."""

    index: str
    id: str
    name: str
    raw: str = ""


_PROC_ASOUND_CARD_RE = re.compile(
    r"^\s*(\d+)\s+\[([^\]]+)\]\s*:\s*(.+)$",
    re.MULTILINE,
)
_ARECORD_CARD_RE = re.compile(
    r"^card\s+(\d+):\s*(\S+)\s*\[([^\]]+)\].*$",
    re.IGNORECASE | re.MULTILINE,
)


def _iter_proc_asound_entries(text: str) -> list[tuple[str, str, str, str, str]]:
    """Yield (index, card_id, rest, detail, raw) from ``/proc/asound/cards``."""
    entries: list[tuple[str, str, str, str, str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        m = re.match(r"^\s*(\d+)\s+\[([^\]]+)\]\s*:\s*(.+)$", line)
        if not m:
            i += 1
            continue
        index, card_id, rest = m.group(1), m.group(2).strip(), m.group(3).strip()
        detail = ""
        if i + 1 < len(lines) and lines[i + 1].strip() and not re.match(
            r"^\s*\d+\s+\[", lines[i + 1]
        ):
            detail = lines[i + 1].strip()
            i += 1
        raw = f"{line} | {detail}".strip(" |")
        entries.append((index, card_id, rest, detail, raw))
        i += 1
    return entries


def parse_proc_asound_cards(text: str) -> list[AlsaUsbCard]:
    """Parse ``/proc/asound/cards``; return USB-Audio entries (not HDMI capture)."""
    cards: list[AlsaUsbCard] = []
    for index, card_id, rest, detail, raw in _iter_proc_asound_entries(text):
        hay = f"{rest} {detail} {card_id}".lower()
        if looks_like_hdmi_capture(hay):
            continue
        if "usb" in hay or "usb-audio" in hay:
            name = detail or rest.split(" - ", 1)[-1].strip() or card_id
            cards.append(
                AlsaUsbCard(index=index, id=card_id, name=name, raw=raw)
            )
    return cards


def parse_proc_asound_hdmi_cards(text: str) -> list[AlsaUsbCard]:
    """Parse ``/proc/asound/cards``; return HDMI / capture-card entries only."""
    cards: list[AlsaUsbCard] = []
    for index, card_id, rest, detail, raw in _iter_proc_asound_entries(text):
        hay = f"{rest} {detail} {card_id}".lower()
        if not looks_like_hdmi_capture(hay):
            continue
        name = detail or rest.split(" - ", 1)[-1].strip() or card_id
        cards.append(AlsaUsbCard(index=index, id=card_id, name=name, raw=raw))
    return cards


def parse_arecord_l(text: str) -> list[AlsaUsbCard]:
    """Parse ``arecord -l`` capture devices; keep USB-looking cards (not HDMI)."""
    cards: list[AlsaUsbCard] = []
    seen: set[str] = set()
    for m in _ARECORD_CARD_RE.finditer(text):
        index, card_id, name = m.group(1), m.group(2), m.group(3).strip()
        hay = f"{card_id} {name} {m.group(0)}".lower()
        if looks_like_hdmi_capture(hay):
            continue
        if "usb" not in hay and "codec" not in hay:
            # Still accept generic UAC labels that omit the word usb.
            if "audio" not in hay:
                continue
        if index in seen:
            continue
        seen.add(index)
        cards.append(AlsaUsbCard(index=index, id=card_id, name=name, raw=m.group(0)))
    return cards


def parse_arecord_l_hdmi(text: str) -> list[AlsaUsbCard]:
    """Parse ``arecord -l``; keep HDMI / capture-card devices only."""
    cards: list[AlsaUsbCard] = []
    seen: set[str] = set()
    for m in _ARECORD_CARD_RE.finditer(text):
        index, card_id, name = m.group(1), m.group(2), m.group(3).strip()
        hay = f"{card_id} {name} {m.group(0)}".lower()
        if not looks_like_hdmi_capture(hay):
            continue
        if index in seen:
            continue
        seen.add(index)
        cards.append(AlsaUsbCard(index=index, id=card_id, name=name, raw=m.group(0)))
    return cards


def discover_alsa_usb_cards(
    *,
    arecord: Optional[str] = None,
    proc_asound_path: str = "/proc/asound/cards",
) -> tuple[list[AlsaUsbCard], str]:
    """Prefer ``/proc/asound/cards``; fall back to ``arecord -l``."""
    cards: list[AlsaUsbCard] = []
    detail_parts: list[str] = []
    try:
        from pathlib import Path

        path = Path(proc_asound_path)
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            cards = parse_proc_asound_cards(text)
            detail_parts.append(f"asound cards → {len(cards)} USB")
    except OSError as exc:
        detail_parts.append(f"asound cards unread: {exc}")

    if cards:
        return cards, "; ".join(detail_parts)

    bin_arecord = arecord if arecord is not None else which("arecord")
    if not bin_arecord:
        detail_parts.append("arecord missing")
        return [], "; ".join(detail_parts) or "no ALSA USB cards"
    try:
        proc = run_cmd([bin_arecord, "-l"], timeout=4.0)
        if proc.returncode == 0 and proc.stdout.strip():
            cards = parse_arecord_l(proc.stdout)
            detail_parts.append(f"arecord -l → {len(cards)} USB")
            return cards, "; ".join(detail_parts)
        detail_parts.append(
            (proc.stderr or proc.stdout or "arecord -l failed").strip()
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        detail_parts.append(f"arecord error: {exc}")
    return cards, "; ".join(detail_parts) or "no ALSA USB cards"


def discover_alsa_hdmi_cards(
    *,
    arecord: Optional[str] = None,
    proc_asound_path: str = "/proc/asound/cards",
) -> tuple[list[AlsaUsbCard], str]:
    """Prefer ``/proc/asound/cards``; fall back to ``arecord -l`` for HDMI/capture."""
    cards: list[AlsaUsbCard] = []
    detail_parts: list[str] = []
    try:
        from pathlib import Path

        path = Path(proc_asound_path)
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            cards = parse_proc_asound_hdmi_cards(text)
            detail_parts.append(f"asound cards → {len(cards)} HDMI/capture")
    except OSError as exc:
        detail_parts.append(f"asound HDMI unread: {exc}")

    if cards:
        return cards, "; ".join(detail_parts)

    bin_arecord = arecord if arecord is not None else which("arecord")
    if not bin_arecord:
        detail_parts.append("arecord missing")
        return [], "; ".join(detail_parts) or "no ALSA HDMI/capture cards"
    try:
        proc = run_cmd([bin_arecord, "-l"], timeout=4.0)
        if proc.returncode == 0 and proc.stdout.strip():
            cards = parse_arecord_l_hdmi(proc.stdout)
            detail_parts.append(f"arecord -l → {len(cards)} HDMI/capture")
            return cards, "; ".join(detail_parts)
        detail_parts.append(
            (proc.stderr or proc.stdout or "arecord -l failed").strip()
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        detail_parts.append(f"arecord HDMI error: {exc}")
    return cards, "; ".join(detail_parts) or "no ALSA HDMI/capture cards"


def alsa_usb_to_pw_node(card: AlsaUsbCard) -> PwNode:
    """Synthesize a PipeWire-ish source name from an ALSA USB card."""
    # PipeWire pulse/ALSA typically: alsa_input.usb-<vendor>_<product>-00...
    safe_id = re.sub(r"[^A-Za-z0-9]+", "_", card.id).strip("_") or "USB"
    safe_name = re.sub(r"[^A-Za-z0-9]+", "_", card.name).strip("_") or safe_id
    pw_name = f"alsa_input.usb-{safe_name}-00"
    return PwNode(
        id=f"alsa:usb:{card.index}",
        name=pw_name,
        description=card.name,
        mac="",
        media_class="Audio/Source",
    )


def alsa_hdmi_to_pw_node(card: AlsaUsbCard) -> PwNode:
    """Synthesize a PipeWire-ish source name from an ALSA HDMI/capture card."""
    safe_id = re.sub(r"[^A-Za-z0-9]+", "_", card.id).strip("_") or "HDMI"
    safe_name = re.sub(r"[^A-Za-z0-9]+", "_", card.name).strip("_") or safe_id
    # Keep usb- prefix when the dongle is UAC; classify still returns hdmi.
    if "usb" in f"{card.id} {card.name} {card.raw}".lower():
        pw_name = f"alsa_input.usb-{safe_name}-00"
    else:
        pw_name = f"alsa_input.hdmi-{safe_name}-00"
    return PwNode(
        id=f"alsa:hdmi:{card.index}",
        name=pw_name,
        description=card.name if looks_like_hdmi_capture(card.name) else f"HDMI {card.name}",
        mac="",
        media_class="Audio/Source",
    )


def merge_alsa_usb_into_sources(
    nodes: list[PwNode],
    cards: list[AlsaUsbCard],
) -> list[PwNode]:
    """Append ALSA-only USB cards not already represented as PipeWire sources."""
    if not cards:
        return nodes
    existing = " ".join(f"{n.name} {n.description}".lower() for n in nodes)
    out = list(nodes)
    for card in cards:
        needles = [
            card.name.lower(),
            card.id.lower(),
            f"usb-{card.id}".lower(),
            f"card {card.index}",
        ]
        if any(n and n in existing for n in needles):
            continue
        # Also skip if any alsa_input.usb already covers a single-card case later.
        synth = alsa_usb_to_pw_node(card)
        out.append(synth)
        existing += f" {synth.name} {synth.description}".lower()
    return out


def merge_alsa_hdmi_into_sources(
    nodes: list[PwNode],
    cards: list[AlsaUsbCard],
) -> list[PwNode]:
    """Append ALSA-only HDMI/capture cards not already in PipeWire sources."""
    if not cards:
        return nodes
    existing = " ".join(f"{n.name} {n.description}".lower() for n in nodes)
    out = list(nodes)
    for card in cards:
        needles = [
            card.name.lower(),
            card.id.lower(),
            f"hdmi-{card.id}".lower(),
            f"card {card.index}",
        ]
        if any(n and n in existing for n in needles):
            continue
        if looks_like_hdmi_capture(existing) and card.name.lower() in existing:
            continue
        synth = alsa_hdmi_to_pw_node(card)
        out.append(synth)
        existing += f" {synth.name} {synth.description}".lower()
    return out


def match_capture_source(
    *,
    endpoint: str,
    name: str,
    nodes: list[PwNode],
    prefer_path: str = "bt_a2dp",
) -> Optional[PwNode]:
    """Match a registered host to a discovered PipeWire source."""
    ep = (endpoint or "").strip()
    if ep:
        for n in nodes:
            if n.id == ep or n.name == ep or ep in n.name:
                return n
        # Endpoint may be a MAC.
        try:
            want = normalize_mac(ep)
        except Exception:  # noqa: BLE001
            want = ""
        if want and ":" in want:
            for n in nodes:
                if n.mac and normalize_mac(n.mac) == want:
                    return n

    want_name = (name or "").strip().lower()
    if want_name:
        for n in nodes:
            hay = f"{n.description} {n.name}".lower()
            if want_name in hay or (
                n.description and n.description.strip().lower() == want_name
            ):
                return n
        # Fuzzy aliases: Galaxy S21 / Laptop demo host names ↔ real devices.
        aliases: list[str] = []
        if "s21" in want_name or "galaxy" in want_name:
            aliases.extend(["s21", "galaxy", "samsung"])
        if "laptop" in want_name or "usb" in want_name or "pc" in want_name:
            aliases.extend(["usb", "audio", "codec", "headset"])
        if "hdmi" in want_name or "capture" in want_name:
            aliases.extend(["hdmi", "capture", "ms2109", "macrosilicon", "elgato"])
        for n in nodes:
            hay = f"{n.description} {n.name}".lower()
            if any(a in hay for a in aliases):
                # Respect preferred capture path when classifying.
                if classify_capture_source(n) == prefer_path or not prefer_path:
                    return n

    # Prefer a single bluez_input when host asks for BT A2DP.
    path_nodes = [
        n
        for n in nodes
        if classify_capture_source(n) == prefer_path and is_capture_candidate(n)
    ]
    if len(path_nodes) == 1:
        return path_nodes[0]
    return None


def build_capture_loopback_commands(
    *,
    host_id: str,
    feeder: str,
    source_ref: str,
    tools: dict[str, Optional[str]],
) -> tuple[list[list[str]], list[str]]:
    """Wire a PipeWire source into musync_feeder (BT / USB / HDMI → fan-out)."""
    cmds: list[list[str]] = []
    notes: list[str] = []
    pactl = tools.get("pactl")
    pw_link = tools.get("pw-link")
    tag = capture_module_tag(host_id)

    if pactl:
        cmds.append(
            [
                pactl,
                "load-module",
                "module-loopback",
                "latency_msec=20",
                f"source={source_ref}",
                f"sink={feeder}",
                "sink_dont_move=true",
                "source_dont_move=true",
                f"sink_input_properties=media.name={tag}",
            ]
        )
        notes.append(f"{host_id}: capture loopback {source_ref} → {feeder}")
    elif pw_link:
        cmds.append([pw_link, f"{source_ref}:capture_FL", f"{feeder}:playback_FL"])
        cmds.append([pw_link, f"{source_ref}:capture_FR", f"{feeder}:playback_FR"])
        notes.append(
            f"{host_id}: pw-link capture (no latency tag) {source_ref} → {feeder}"
        )
    else:
        notes.append(
            f"{host_id}: no pactl/pw-link — cannot route {source_ref} into {feeder}"
        )
    return cmds, notes


def capture_module_tag(host_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", host_id)
    return f"musync_capture_{safe}"


def find_musync_capture_ids(modules_text: str, host_id: str) -> list[str]:
    tag = capture_module_tag(host_id)
    found: list[str] = []
    for mid, rest in parse_pactl_modules_short(modules_text):
        if tag in rest or f"musync_capture_{host_id}" in rest:
            found.append(mid)
    return found


@dataclass
class BtRemote:
    """A bluetoothctl-known remote (paired / connected speaker)."""

    mac: str
    name: str
    paired: bool = False
    connected: bool = False


@dataclass
class BtAdapter:
    """One BlueZ HCI controller (onboard Pi BT or USB BT600)."""

    hci: str
    mac: str = ""
    name: str = ""
    powered: bool = False
    healthy: bool = True
    onboard: bool = False
    detail: str = ""


# Short timeouts so a dead RTL8761 / BT600 cannot stall the API or reconnect loop.
BT_ADAPTER_PROBE_TIMEOUT_S = 1.5
BT_CONNECT_SOFT_TIMEOUT_S = 3.5
BT_PAIR_SOFT_TIMEOUT_S = 6.0


def parse_bluetoothctl_list(text: str) -> list[BtAdapter]:
    """Parse ``bluetoothctl list`` lines: ``Controller AA:BB:… hci0 [default]``."""
    adapters: list[BtAdapter] = []
    line_re = re.compile(
        r"^Controller\s+([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\s+(\S+)(?:\s+(.*))?$"
    )
    for raw in text.splitlines():
        m = line_re.match(raw.strip())
        if not m:
            continue
        mac = normalize_mac(m.group(1))
        token = m.group(2).strip()
        rest = (m.group(3) or "").strip()
        hci = ""
        name = ""
        # Formats: "hci0 [default]" | "My Pi" | "hci1"
        if re.fullmatch(r"hci\d+", token, re.IGNORECASE):
            hci = token.lower()
            name = rest.replace("[default]", "").strip() or hci
        elif re.search(r"hci\d+", rest, re.IGNORECASE):
            found = re.search(r"(hci\d+)", rest, re.IGNORECASE)
            hci = found.group(1).lower() if found else ""
            name = token
        else:
            name = token
            # Fall back: look for hciN anywhere on the line.
            found = re.search(r"(hci\d+)", raw, re.IGNORECASE)
            hci = found.group(1).lower() if found else ""
        if not hci:
            continue
        adapters.append(BtAdapter(hci=hci, mac=mac, name=name or hci))
    return adapters


def list_sysfs_bluetooth_hcis() -> list[str]:
    """Return ``hciN`` names under ``/sys/class/bluetooth`` (empty off-Pi)."""
    import os

    root = "/sys/class/bluetooth"
    try:
        names = [
            name
            for name in os.listdir(root)
            if re.fullmatch(r"hci\d+", name, re.IGNORECASE)
        ]
    except OSError:
        return []
    return sorted(names, key=lambda n: int(re.sub(r"\D", "", n) or "0"))


def adapter_sysfs_onboard(hci: str) -> bool:
    """True when the HCI looks like Pi onboard (UART/platform), not USB."""
    import os

    base = f"/sys/class/bluetooth/{hci}"
    try:
        device = os.path.realpath(os.path.join(base, "device"))
    except OSError:
        return False
    low = device.lower()
    if "/usb" in low:
        return False
    # Onboard Pi BT is typically uart / serial / platform.
    return any(tok in low for tok in ("serial", "uart", "platform", "soc"))


def parse_bluetoothctl_show(text: str) -> dict[str, str]:
    """Best-effort parse of ``bluetoothctl show`` / ``show <mac>``."""
    info: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        info[key.strip().lower()] = val.strip()
    return info


def discover_bluetooth_adapters(
    *,
    bluetoothctl: Optional[str],
    probe: bool = True,
    probe_timeout: float = BT_ADAPTER_PROBE_TIMEOUT_S,
) -> tuple[list[BtAdapter], str]:
    """List HCI adapters; soft-mark unresponsive BT600s without blocking long.

    Never raises. Dead USB sticks that hang ``show`` are marked ``healthy=False``
    after a short timeout so reconnect/connect can skip them.
    """
    adapters: list[BtAdapter] = []
    if bluetoothctl:
        try:
            proc = run_cmd([bluetoothctl, "list"], timeout=3.0)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return [], f"bluetoothctl list error: {exc}"
        if proc.returncode == 0:
            adapters = parse_bluetoothctl_list(proc.stdout or "")
        else:
            detail = (proc.stderr or proc.stdout or "bluetoothctl list failed").strip()
            # Fall through to sysfs — still useful when bluez is half-up.
            adapters = []
            sys_hcis = list_sysfs_bluetooth_hcis()
            if not sys_hcis:
                return [], detail
            for hci in sys_hcis:
                adapters.append(
                    BtAdapter(
                        hci=hci,
                        onboard=adapter_sysfs_onboard(hci),
                        healthy=False,
                        detail="listed via sysfs only",
                    )
                )
            return adapters, f"{detail}; sysfs → {len(adapters)} hci"

    if not adapters:
        # No bluetoothctl or empty list — try sysfs alone.
        for hci in list_sysfs_bluetooth_hcis():
            adapters.append(
                BtAdapter(
                    hci=hci,
                    onboard=adapter_sysfs_onboard(hci),
                    healthy=bool(bluetoothctl),
                    detail="sysfs" if not bluetoothctl else "hci present",
                )
            )
        if not adapters:
            return [], "no Bluetooth adapters"
        if not bluetoothctl:
            return adapters, f"sysfs → {len(adapters)} hci (bluetoothctl missing)"

    # Enrich powered / healthy with a hard short timeout per adapter.
    for adapter in adapters:
        if not adapter.onboard:
            adapter.onboard = adapter_sysfs_onboard(adapter.hci)
        if not probe or not bluetoothctl:
            continue
        try:
            # Prefer show <mac> so we do not rely on global default controller.
            target = adapter.mac or adapter.hci
            proc = run_cmd(
                [bluetoothctl, "show", target],
                timeout=probe_timeout,
            )
        except subprocess.TimeoutExpired:
            adapter.healthy = False
            adapter.powered = False
            adapter.detail = f"probe timeout {probe_timeout:.1f}s — skip (dead BT600?)"
            log.warning("BT adapter %s probe timed out — treating as dead", adapter.hci)
            continue
        except OSError as exc:
            adapter.healthy = False
            adapter.powered = False
            adapter.detail = f"probe error: {exc}"
            continue
        if proc.returncode != 0:
            out = ((proc.stdout or "") + (proc.stderr or "")).strip()
            adapter.healthy = False
            adapter.powered = False
            adapter.detail = out or f"show failed (rc={proc.returncode})"
            continue
        info = parse_bluetoothctl_show(proc.stdout or "")
        powered = info.get("powered", "").lower()
        adapter.powered = powered in {"yes", "true", "1"}
        name = info.get("name") or info.get("alias")
        if name:
            adapter.name = name
        adapter.healthy = True
        adapter.detail = "powered" if adapter.powered else "present (powered off)"

    healthy_n = sum(1 for a in adapters if a.healthy)
    onboard_n = sum(1 for a in adapters if a.onboard)
    return (
        adapters,
        f"adapters={len(adapters)} healthy={healthy_n} onboard={onboard_n}",
    )


def bluetoothctl_select(
    bluetoothctl: Optional[str],
    adapter: str,
    *,
    timeout: float = BT_ADAPTER_PROBE_TIMEOUT_S,
) -> tuple[bool, str]:
    """Select an HCI by MAC or ``hciN`` before pair/connect (multi-adapter)."""
    if not bluetoothctl:
        return False, "bluetoothctl missing"
    want = (adapter or "").strip()
    if not want:
        return False, "empty adapter"
    try:
        proc = run_cmd([bluetoothctl, "select", want], timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"select {want}: timeout"
    except OSError as exc:
        return False, f"select {want}: {exc}"
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode == 0:
        return True, out or f"selected {want}"
    return False, out or f"select {want} failed (rc={proc.returncode})"


def bluetoothctl_power_on(
    bluetoothctl: Optional[str],
    *,
    timeout: float = BT_ADAPTER_PROBE_TIMEOUT_S,
) -> tuple[bool, str]:
    """Best-effort ``power on`` for the currently selected adapter."""
    if not bluetoothctl:
        return False, "bluetoothctl missing"
    try:
        proc = run_cmd([bluetoothctl, "power", "on"], timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "power on: timeout"
    except OSError as exc:
        return False, f"power on: {exc}"
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    low = out.lower()
    if proc.returncode == 0 or "succeeded" in low or "already" in low:
        return True, out or "power on"
    return False, out or f"power on failed (rc={proc.returncode})"


def pick_free_adapter(
    adapters: list[BtAdapter],
    *,
    used_hcis: set[str],
    preferred_hci: str = "",
) -> Optional[BtAdapter]:
    """Choose a healthy free HCI for the next A2DP sink (prefer preferred, then onboard)."""
    healthy = [a for a in adapters if a.healthy]
    if preferred_hci:
        want = preferred_hci.strip().lower()
        for a in healthy:
            if a.hci == want and a.hci not in used_hcis:
                return a
    # Prefer free onboard first (known-good Qudelix path), then USB BT600s.
    ordered = sorted(
        healthy,
        key=lambda a: (0 if a.onboard else 1, int(re.sub(r"\D", "", a.hci) or "99")),
    )
    for a in ordered:
        if a.hci not in used_hcis:
            return a
    return None


def adapter_capacity_note(adapters: list[BtAdapter]) -> str:
    """Human soft-fail note when only onboard (or fewer) adapters are healthy."""
    healthy = [a for a in adapters if a.healthy]
    n = len(healthy)
    if n == 0:
        return (
            "No healthy Bluetooth adapters — BT outputs stay MISSING; "
            "onboard / BT600 soft-fail (API stays up)"
        )
    if n == 1:
        label = "onboard" if healthy[0].onboard else healthy[0].hci
        return (
            f"Only 1 healthy adapter ({label}) — one A2DP sink max; "
            "extra BT600s soft-fail until plugged / healthy"
        )
    return f"{n} healthy adapters · up to {min(n, 6)} A2DP sinks"


def parse_bluetoothctl_devices(text: str) -> list[BtRemote]:
    """Parse ``bluetoothctl devices`` lines: ``Device AA:BB:… Name``."""
    remotes: list[BtRemote] = []
    line_re = re.compile(
        r"^Device\s+([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\s+(.+)$"
    )
    for raw in text.splitlines():
        m = line_re.match(raw.strip())
        if not m:
            continue
        remotes.append(
            BtRemote(mac=normalize_mac(m.group(1)), name=m.group(2).strip())
        )
    return remotes


def parse_bluetoothctl_info(text: str) -> dict[str, str]:
    """Best-effort key/value parse of ``bluetoothctl info <mac>``."""
    info: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        info[key.strip().lower()] = val.strip()
    return info


def discover_bluetooth_remotes(
    *,
    bluetoothctl: Optional[str],
) -> tuple[list[BtRemote], str]:
    """List paired/known BT remotes with friendly names (no MAC typing needed)."""
    if not bluetoothctl:
        return [], "bluetoothctl missing"
    try:
        proc = run_cmd([bluetoothctl, "devices"], timeout=4.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], f"bluetoothctl devices error: {exc}"
    if proc.returncode != 0:
        return [], (proc.stderr or proc.stdout or "bluetoothctl devices failed").strip()

    remotes = parse_bluetoothctl_devices(proc.stdout or "")
    # Enrich paired/connected flags when cheap.
    for remote in remotes:
        try:
            info_proc = run_cmd(
                [bluetoothctl, "info", remote.mac], timeout=3.0
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if info_proc.returncode != 0:
            continue
        info = parse_bluetoothctl_info(info_proc.stdout or "")
        name = info.get("name") or info.get("alias")
        if name:
            remote.name = name
        paired = info.get("paired", "").lower()
        connected = info.get("connected", "").lower()
        remote.paired = paired in {"yes", "true", "1"}
        remote.connected = connected in {"yes", "true", "1"}
    return remotes, f"bluetoothctl → {len(remotes)} remotes"


def bluetoothctl_scan_on(
    bluetoothctl: Optional[str],
    *,
    timeout: float = 3.0,
) -> tuple[bool, str]:
    """Start BlueZ discovery (``bluetoothctl scan on``). Non-blocking for the API."""
    if not bluetoothctl:
        return False, "bluetoothctl missing"
    try:
        proc = run_cmd([bluetoothctl, "scan", "on"], timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        # scan on often keeps the process open; TimeoutExpired still means scan started.
        if isinstance(exc, subprocess.TimeoutExpired):
            return True, "scan on (background)"
        return False, f"scan on: {exc}"
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    low = out.lower()
    if proc.returncode == 0 or "discovery started" in low or "already" in low:
        return True, out or "scan on"
    # Some builds print to stdout and still start discovery.
    if "failed" in low or "not available" in low:
        return False, out or f"scan on failed (rc={proc.returncode})"
    return True, out or "scan on"


def bluetoothctl_scan_off(
    bluetoothctl: Optional[str],
    *,
    timeout: float = 4.0,
) -> tuple[bool, str]:
    """Stop BlueZ discovery (``bluetoothctl scan off``)."""
    if not bluetoothctl:
        return False, "bluetoothctl missing"
    try:
        proc = run_cmd([bluetoothctl, "scan", "off"], timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"scan off: {exc}"
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    low = out.lower()
    if proc.returncode == 0 or "discovery stopped" in low or "already" in low:
        return True, out or "scan off"
    return False, out or f"scan off failed (rc={proc.returncode})"


def bluetoothctl_pair(
    bluetoothctl: Optional[str],
    mac: str,
    *,
    timeout: float = BT_PAIR_SOFT_TIMEOUT_S,
) -> tuple[bool, str]:
    """Best-effort ``bluetoothctl pair <mac>`` before connect.

    Short default timeout — a hung BT600 must not block the API.
    """
    if not bluetoothctl:
        return False, "bluetoothctl missing"
    want = normalize_mac(mac)
    if not want or want.count(":") != 5:
        return False, f"invalid mac: {mac!r}"
    try:
        proc = run_cmd([bluetoothctl, "pair", want], timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"pair {want}: timeout {timeout:.1f}s"
    except OSError as exc:
        return False, f"pair {want}: {exc}"
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    low = out.lower()
    if (
        proc.returncode == 0
        or "pairing successful" in low
        or "already paired" in low
        or "exists" in low
    ):
        return True, out or f"paired {want}"
    return False, out or f"pair {want} failed (rc={proc.returncode})"


def bluetoothctl_connect(
    bluetoothctl: Optional[str],
    mac: str,
    *,
    timeout: float = BT_CONNECT_SOFT_TIMEOUT_S,
) -> tuple[bool, str]:
    """Soft ``bluetoothctl connect <mac>`` for demo reboot recovery.

    Returns ``(ok, detail)``. Already-connected remotes usually succeed quickly.
    Never raises — demo path prefers MISSING over a hung API. Default timeout is
    short so dead BT600 dongles cannot stall multi-sink reconnect.
    """
    if not bluetoothctl:
        return False, "bluetoothctl missing"
    want = normalize_mac(mac)
    if not want or want.count(":") != 5:
        return False, f"invalid mac: {mac!r}"
    try:
        proc = run_cmd([bluetoothctl, "connect", want], timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"connect {want}: timeout {timeout:.1f}s"
    except OSError as exc:
        return False, f"connect {want}: {exc}"
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    low = out.lower()
    if proc.returncode == 0 or "connection successful" in low or "already connected" in low:
        return True, out or f"connected {want}"
    return False, out or f"connect {want} failed (rc={proc.returncode})"


def friendly_bluez_label(node: PwNode, remotes_by_mac: dict[str, BtRemote]) -> str:
    """Prefer bluetoothctl name, then node description, then truncated node name."""
    if node.mac:
        remote = remotes_by_mac.get(normalize_mac(node.mac))
        if remote and remote.name:
            return remote.name
    desc = (node.description or "").strip()
    # wpctl often appends "[vol: 0.80]" — strip that.
    desc = re.sub(r"\[vol:[^\]]*\]", "", desc).strip()
    if desc and not desc.lower().startswith("bluez"):
        return desc
    name = node.name
    if node.mac:
        return f"BT {normalize_mac(node.mac)}"
    return name or f"node-{node.id}"


def match_device_node(
    *,
    mac: str,
    hci: str,
    pw_node_override: str,
    nodes: list[PwNode],
    name: str = "",
) -> Optional[PwNode]:
    """Match a configured device to a discovered PipeWire node (MAC, id, or name)."""
    if pw_node_override:
        for n in nodes:
            if n.id == pw_node_override or n.name == pw_node_override:
                return n
        # Allow override even if not discovered yet (manual WirePlumber id).
        return PwNode(id=pw_node_override, name=pw_node_override, mac=normalize_mac(mac) if mac else "")

    want = normalize_mac(mac) if mac else ""
    if want:
        suffix = mac_to_bluez_suffix(want)
        for n in nodes:
            if n.mac and normalize_mac(n.mac) == want:
                return n
            if suffix and suffix.lower() in n.name.lower():
                return n

    # Match by friendly speaker name (Qudelix etc.) when MAC not yet known.
    want_name = (name or "").strip().lower()
    if want_name:
        for n in nodes:
            hay = f"{n.description} {n.name}".lower()
            if want_name in hay or (n.description and n.description.strip().lower() == want_name):
                return n

    # Last resort: name contains hci tag (rare; mostly for tests/fixtures).
    if hci:
        for n in nodes:
            if hci.lower() in n.name.lower() or hci.lower() in n.description.lower():
                return n
    return None


def build_sink_commands(
    *,
    device_id: str,
    feeder: str,
    target_node_id: str,
    target_name: str,
    delay_ms: int,
    volume: float,
    mute: bool,
    tools: dict[str, Optional[str]],
) -> tuple[list[list[str]], list[str]]:
    """Commands to wire one sink: loopback(latency) + volume/mute + links.

    Preferred on Pi (PipeWire pulse compat)::

      pactl load-module module-loopback latency_msec=<delay> \\
        source=<feeder>.monitor sink=<bluez_sink>

    Fallback when pactl missing: ``pw-link`` feeder → bluez (no delay) + wpctl
    volume. Returns ``(commands, notes)``.
    """
    cmds: list[list[str]] = []
    notes: list[str] = []
    pactl = tools.get("pactl")
    wpctl = tools.get("wpctl")
    pw_link = tools.get("pw-link")

    sink_ref = target_name or target_node_id
    tag = loopback_module_tag(device_id)

    if pactl:
        # Caller should unload prior module index first; load creates a new one.
        cmds.append(
            [
                pactl,
                "load-module",
                "module-loopback",
                f"latency_msec={max(0, int(delay_ms))}",
                f"source={feeder}.monitor",
                f"sink={sink_ref}",
                "sink_dont_move=true",
                "source_dont_move=true",
                # Helps identify MuSync-owned modules in `pactl list modules short`.
                f"sink_input_properties=media.name={tag}",
            ]
        )
        notes.append(
            f"{device_id}: loopback latency_msec={delay_ms} via pactl → {sink_ref}"
        )
    elif pw_link:
        cmds.append([pw_link, f"{feeder}:monitor_FL", f"{sink_ref}:playback_FL"])
        cmds.append([pw_link, f"{feeder}:monitor_FR", f"{sink_ref}:playback_FR"])
        notes.append(
            f"{device_id}: pw-link only (no delay) — install pulseaudio-utils "
            f"for pactl module-loopback latency_msec"
        )
    else:
        notes.append(
            f"{device_id}: no pactl/pw-link — cannot wire graph for {sink_ref}"
        )

    if wpctl and target_node_id:
        cmds.append([wpctl, "set-volume", target_node_id, f"{volume:.3f}"])
        cmds.append([wpctl, "set-mute", target_node_id, "1" if mute else "0"])
    elif not wpctl:
        notes.append(f"{device_id}: wpctl missing — volume/mute not applied on Pi")

    return cmds, notes


def loopback_module_tag(device_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", device_id)
    return f"musync_loopback_{safe}"


def parse_pactl_modules_short(text: str) -> list[tuple[str, str]]:
    """Return [(module_id, rest_of_line), ...] from `pactl list modules short`."""
    rows: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) == 1:
            rows.append((parts[0], ""))
        else:
            rows.append((parts[0], parts[1]))
    return rows


def find_musync_loopback_ids(modules_text: str, device_id: str) -> list[str]:
    tag = loopback_module_tag(device_id)
    found: list[str] = []
    for mid, rest in parse_pactl_modules_short(modules_text):
        if tag in rest or f"musync_loopback_{device_id}" in rest:
            found.append(mid)
    return found


def find_feeder_null_sink_ids(
    modules_text: str,
    feeder_name: str = "musync_feeder",
) -> list[str]:
    """Module indexes for ``module-null-sink`` owned by MuSync's feeder.

    Matches ``sink_name=<feeder>`` (and ``.2`` suffixes) or description
    ``MuSync_Feeder``. Used to purge orphan feeders after repeated soft-loads.
    """
    name = (feeder_name or "musync_feeder").strip() or "musync_feeder"
    found: list[str] = []
    for mid, rest in parse_pactl_modules_short(modules_text):
        if "module-null-sink" not in rest:
            continue
        hay = rest.lower()
        if f"sink_name={name.lower()}" in hay or f"sink_name={name.lower()}." in hay:
            found.append(mid)
            continue
        if "musync_feeder" in hay or "device.description=musync_feeder" in hay:
            found.append(mid)
    return found


def feeder_listed_in_sinks(sinks_short_text: str, feeder_name: str = "musync_feeder") -> bool:
    """True if ``pactl list sinks short`` already has the feeder sink."""
    name = (feeder_name or "musync_feeder").strip() or "musync_feeder"
    for raw in sinks_short_text.splitlines():
        # "42\tmusync_feeder\t…" or space-separated
        parts = raw.strip().split()
        if len(parts) >= 2 and parts[1] == name:
            return True
        if name in raw.split():
            # also accept musync_feeder.2 style orphans as "present" for purge path
            for tok in parts:
                if tok == name or tok.startswith(f"{name}."):
                    return True
    return False
