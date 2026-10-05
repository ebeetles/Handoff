"""Offline tests for fetch_audius.py's selection, naming, and overrides logic.
Track dicts mirror real /v1/tracks responses (see swagger `track` schema)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fetch_audius as F  # noqa: E402


def track(**kw):
    t = {"id": "PkglRZ1", "title": "Tropical House Bandlab", "permalink": "/teixo/tropical-house-bandlab",
         "user": {"name": "djteixo", "handle": "teixo"}, "is_downloadable": True, "is_download_gated": False,
         "access": {"stream": True, "download": True}, "is_delete": False, "is_available": True,
         "license": "All rights reserved", "orig_filename": "Tropical House Bandlab - [djteixo].mp3"}
    t.update(kw)
    return t


def test_skip_reason_requires_downloadable_and_access():
    assert F.skip_reason(track()) is None
    assert "not downloadable" in F.skip_reason(track(is_downloadable=False))
    # Seen live: is_downloadable true but gated, access.download false.
    assert "gated" in F.skip_reason(track(is_download_gated=True, access={"stream": True, "download": False}))
    # Seen live: access.download true but is_downloadable false. Still skip.
    assert "not downloadable" in F.skip_reason(track(is_downloadable=False, access={"download": True}))
    assert F.skip_reason(track(access=None)) is not None   # "required" fields can come back null
    assert F.skip_reason(track(is_delete=True)) == "deleted"


def test_filename_is_artist_dash_title_and_filesystem_safe():
    assert F.base_name(track()) == "djteixo - Tropical House Bandlab"
    assert F.base_name(track(title='A/B: "C"?', user={"name": "X|Y"})) == "X Y - A B C"
    assert F.base_name(track(user={"handle": "h"})) == "h - Tropical House Bandlab"


def test_pick_extension_prefers_server_filename():
    assert F.pick_extension("audio/mpeg", "x.mp3", "x.wav") == ".mp3"
    assert F.pick_extension("audio/flac", None, "orig.FLAC") == ".flac"
    assert F.pick_extension("audio/x-wav; charset=binary", None, None) == ".wav"
    assert F.pick_extension(None, "weird.bin", None) == ".mp3"


def test_override_entry_has_credit_with_url():
    e = F.override_entry(track())
    assert e["credit"] == "djteixo — https://audius.co/teixo/tropical-house-bandlab"
    assert e["license"] == "All rights reserved"
    assert F.override_entry(track(license=None))["license"].startswith("Not specified")


def test_merge_overrides_never_clobbers():
    existing = {"Other.mp3": {"bpm_hint": 128},
                "djteixo - Tropical House Bandlab.mp3": {"downbeat_shift": 1, "license": "CC BY 4.0"}}
    added = F.merge_overrides(existing, "djteixo - Tropical House Bandlab.mp3", F.override_entry(track()))
    entry = existing["djteixo - Tropical House Bandlab.mp3"]
    assert entry["downbeat_shift"] == 1 and entry["license"] == "CC BY 4.0"
    assert set(added) == {"title", "artist", "credit"}
    assert existing["Other.mp3"] == {"bpm_hint": 128}
    assert F.merge_overrides(existing, "djteixo - Tropical House Bandlab.mp3", F.override_entry(track())) == []


def test_refs_and_resolve_location():
    assert F.is_url("https://audius.co/teixo/tropical-house-bandlab")
    assert F.is_url("teixo/tropical-house-bandlab")
    assert not F.is_url("PkglRZ1")
    assert F.track_id_from_location("/v1/tracks/4bAJ1Ed") == "4bAJ1Ed"   # live /resolve response
    assert F.track_id_from_location("/v1/users/abc") is None


def test_api_key_not_forwarded_to_content_node():
    import email.message
    import io
    req = F.AudiusClient("k")._request("/tracks/x/download")
    assert req.get_header("X-api-key") == "k"
    h = F._StripKeyOffHost()
    off = h.redirect_request(req, io.BytesIO(), 302, "Found", email.message.Message(),
                             "https://val003.open-audio-validator.com/tracks/cidstream/x")
    assert off.get_header("X-api-key") is None
    same = h.redirect_request(req, io.BytesIO(), 302, "Found", email.message.Message(), "https://api.audius.co/v1/tracks/x")
    assert same.get_header("X-api-key") == "k"
    assert F.AudiusClient(None)._request("/tracks/x").get_header("X-api-key") is None
