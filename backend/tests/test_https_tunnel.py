"""HTTPS / Cloudflare tunnel helper for phone Spotify OAuth."""

from __future__ import annotations

from musync.streaming.https_tunnel import (
    bash_https_tunnel_script,
    build_https_tunnel_info,
    https_callback_from_tunnel_base,
)


def test_https_callback_from_tunnel_base() -> None:
    assert (
        https_callback_from_tunnel_base("https://abc.trycloudflare.com")
        == "https://abc.trycloudflare.com/api/streaming/spotify/callback"
    )
    assert (
        https_callback_from_tunnel_base("abc.trycloudflare.com")
        == "https://abc.trycloudflare.com/api/streaming/spotify/callback"
    )
    assert (
        https_callback_from_tunnel_base(
            "https://abc.trycloudflare.com/api/streaming/spotify/callback"
        )
        == "https://abc.trycloudflare.com/api/streaming/spotify/callback"
    )


def test_build_https_tunnel_info(monkeypatch) -> None:
    monkeypatch.setenv("MUSYNC_PORT", "8080")
    info = build_https_tunnel_info()
    assert info["port"] == 8080
    assert info["command"] == "cloudflared tunnel --url http://127.0.0.1:8080"
    assert info["example_redirect_uri"].startswith("https://")
    assert "/api/streaming/spotify/callback" in info["example_redirect_uri"]
    assert info["script_sh"].endswith("https-tunnel.sh")
    assert any("cloudflared" in s for s in info["steps"])
    assert "LAN-IP" in info["hint"] or "LAN" in info["hint"]


def test_bash_https_tunnel_script() -> None:
    body = bash_https_tunnel_script(port=8080)
    assert body.startswith("#!/usr/bin/env bash")
    assert "cloudflared tunnel --url" in body
    assert 'PORT="8080"' in body
    assert "127.0.0.1:${PORT}" in body
