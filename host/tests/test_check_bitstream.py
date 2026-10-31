import hashlib
import json

import scripts.check_bitstream as check_bitstream


def test_check_bitstream_reports_manifest_mtime_mismatch_without_rejecting(
    monkeypatch, tmp_path, capsys
):
    bitstream = tmp_path / "host_bd_wrapper.bit"
    bitstream.write_bytes(b"bitstream")
    manifest = tmp_path / "build_manifest.json"
    manifest.write_text(json.dumps({
        "manifest_version": 2,
        "rtl_hash": "current-hash",
        "bitstream": "host_bd_wrapper.bit",
        "bitstream_sha256": hashlib.sha256(b"bitstream").hexdigest(),
        "bitstream_mtime": "2000-01-01 00:00:00",
    }), encoding="utf-8")

    monkeypatch.setattr(check_bitstream, "ROOT", tmp_path)
    monkeypatch.setattr(check_bitstream, "BIT", bitstream)
    monkeypatch.setattr(check_bitstream, "MANIFEST", manifest)
    monkeypatch.setattr(check_bitstream, "hash_rtl", lambda: "current-hash")

    assert check_bitstream.main() == 0
    assert "NOTE: bitstream mtime" in capsys.readouterr().out


def test_check_bitstream_rejects_changed_bitstream_content(monkeypatch, tmp_path, capsys):
    bitstream = tmp_path / "host_bd_wrapper.bit"
    bitstream.write_bytes(b"new-bitstream")
    manifest = tmp_path / "build_manifest.json"
    manifest.write_text(json.dumps({
        "manifest_version": 2,
        "rtl_hash": "current-hash",
        "bitstream": "host_bd_wrapper.bit",
        "bitstream_sha256": hashlib.sha256(b"old-bitstream").hexdigest(),
        "bitstream_mtime": check_bitstream.time.strftime(
            "%Y-%m-%d %H:%M:%S", check_bitstream.time.localtime(bitstream.stat().st_mtime)
        ),
    }), encoding="utf-8")

    monkeypatch.setattr(check_bitstream, "ROOT", tmp_path)
    monkeypatch.setattr(check_bitstream, "BIT", bitstream)
    monkeypatch.setattr(check_bitstream, "MANIFEST", manifest)
    monkeypatch.setattr(check_bitstream, "hash_rtl", lambda: "current-hash")

    assert check_bitstream.main() == 1
    assert "SHA-256" in capsys.readouterr().out


def test_check_bitstream_rejects_invalid_manifest_without_traceback(
    monkeypatch, tmp_path, capsys
):
    bitstream = tmp_path / "host_bd_wrapper.bit"
    bitstream.write_bytes(b"bitstream")
    manifest = tmp_path / "build_manifest.json"
    manifest.write_text("{not-json", encoding="utf-8")

    monkeypatch.setattr(check_bitstream, "ROOT", tmp_path)
    monkeypatch.setattr(check_bitstream, "BIT", bitstream)
    monkeypatch.setattr(check_bitstream, "MANIFEST", manifest)

    assert check_bitstream.main() == 1
    output = capsys.readouterr().out
    assert "manifest" in output
    assert "Traceback" not in output


def test_check_bitstream_rejects_malformed_field_types_without_traceback(
    monkeypatch, tmp_path, capsys
):
    bitstream = tmp_path / "host_bd_wrapper.bit"
    bitstream.write_bytes(b"bitstream")
    manifest = tmp_path / "build_manifest.json"
    manifest.write_text(json.dumps({
        "manifest_version": 2,
        "rtl_hash": 123,
        "bitstream": "host_bd_wrapper.bit",
        "bitstream_sha256": hashlib.sha256(b"bitstream").hexdigest(),
    }), encoding="utf-8")

    monkeypatch.setattr(check_bitstream, "ROOT", tmp_path)
    monkeypatch.setattr(check_bitstream, "BIT", bitstream)
    monkeypatch.setattr(check_bitstream, "MANIFEST", manifest)
    monkeypatch.setattr(check_bitstream, "hash_rtl", lambda: "current-hash")

    assert check_bitstream.main() == 1
    output = capsys.readouterr().out
    assert "RTL" in output
    assert "Traceback" not in output
