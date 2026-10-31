"""Actual native serve replies must pass owned-client entry and closing gates.

The native bridge uses fake PL/SPI and a byte transport; it cannot reach a board.
It lives outside the qualified native build-input map, leaving the ARM ELF valid.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rf_headroom as H
import rf_status_client as C

ROOT = Path(__file__).resolve().parents[3]
NATIVE = ROOT / 'rf_overlay/sw/board_app'
SID = '0123456789abcdef0123456789abcdef'


@pytest.fixture(scope='module')
def native_exchange(tmp_path_factory):
    out = tmp_path_factory.mktemp('native-lease')
    compiler = shutil.which('gcc')
    if compiler is None and os.name == 'nt':
        compiler = '<LOCAL_PATH>'
    assert compiler and Path(compiler).exists(), 'native gcc required, no fake-response fallback'
    exe = out / ('bridge.exe' if os.name == 'nt' else 'bridge')
    sources = ['rf_pl_status_reader.c', 'rf_daemon.c', 'rf_headroom.c',
               'rf_sha256.c', 'rf_daemon_main.c', 'rf_transport_policy.c',
               'rf_protocol.c', 'rf_state.c', 'rf_iq.c', 'rf_sleep.c',
               'rf_core.c', 'platform_rf.c', 'selftest.c', 'test/fake_pl.c',
               'vendor/ad9361.c', 'vendor/ad9361_api.c',
               'vendor/ad9361_conv.c', 'vendor/util.c']
    cmd = [compiler, '-O1', '-I.', '-Ivendor', '-Itest', '-DPLATFORM_HOST',
           '-D_GNU_SOURCE', '-UWIN32', '-include', 'test/host_compat.h']
    cmd += sources + [str(Path(__file__).with_name('native_headroom_lease_bridge.c')),
                      '-o', str(exe), '-lm']
    (out / 'build-command.json').write_text(json.dumps(cmd, indent=2), encoding='utf-8')
    build = subprocess.run(cmd, cwd=str(NATIVE), capture_output=True, text=True)
    (out / 'build.log').write_text(build.stdout + build.stderr, encoding='utf-8')
    assert build.returncode == 0, build.stdout + build.stderr
    requests = [('protocol.hello', {'major': 1, 'minor': 0}),
                ('device.info', {}), ('state.get', {}), ('pl.status.get', {}),
                ('device.info', {}), ('control.arm', {'token': '<SET_LOCALLY>'}),
                ('device.info', {}),
                ('headroom.begin', H.HeadroomPlan().begin_args(SID, cable_confirmed=True)),
                ('headroom.abort', {'study': H.STUDY, 'session_id': SID}),
                ('device.info', {}), ('state.get', {}), ('pl.status.get', {})]
    wire = [{'id': i + 1, 'op': op, 'args': args}
            for i, (op, args) in enumerate(requests)]
    path = out / 'requests.ndjson'
    path.write_text(''.join(json.dumps(r) + '\n' for r in wire), encoding='utf-8')
    run = subprocess.run([str(exe), str(path)], capture_output=True, text=True)
    (out / 'native-replies.ndjson').write_text(run.stdout, encoding='utf-8')
    (out / 'native-run.log').write_text(run.stderr, encoding='utf-8')
    assert run.returncode == 0, run.stdout + run.stderr
    lines = [json.loads(line) for line in run.stdout.splitlines()]
    assert len(lines) == len(wire) and all(line.get('ok') is True for line in lines)
    metadata = json.loads(run.stderr.split('LEASE_BRIDGE ')[-1].strip())
    return wire, lines, metadata


class NativeReplies:
    """Replay unchanged native output only against its exact matching requests."""
    def __init__(self, wire, replies):
        self.wire = list(wire)
        self.replies = list(replies)
        self.buffer = b''
    def settimeout(self, timeout): pass
    def close(self): pass
    def sendall(self, payload):
        assert json.loads(payload) == self.wire.pop(0)
        self.buffer += json.dumps(self.replies.pop(0)).encode() + b'\n'
    def recv(self, size):
        data, self.buffer = self.buffer[:size], self.buffer[size:]
        return data


def test_actual_native_lease_entry_and_abort_detach_are_accepted(native_exchange, monkeypatch):
    wire, replies, meta = native_exchange
    assert replies[2]['result']['lease_held'] is True
    assert replies[10]['result']['lease_held'] is True  # After owned-core detach.
    assert meta['second_busy'] == 1 and meta['second_closed'] == 1
    assert meta['acquire_calls'] == 1 and meta['release_calls'] == 1
    assert meta['lease_after_disconnect'] is False
    socket = NativeReplies(wire, replies)
    monkeypatch.setattr(C.socket, 'create_connection', lambda *a, **kw: socket)
    client = C.RfStatusClient()
    with client.headroom_lease():
        live = client.headroom_capability()
        assert H.capability_available(live), live
        client.headroom_arm('s3cret')
        client.headroom_begin(H.HeadroomPlan().begin_args(SID, cable_confirmed=True))
        client.headroom_abort({'study': H.STUDY, 'session_id': SID})
        closing = client.headroom_closing_readback()
        assert closing['confirmed'] is True, closing
        assert H._closing_confirmed(closing) is True
    assert not socket.wire and not socket.replies


def test_actual_native_closing_preserves_owned_lease(native_exchange, monkeypatch):
    wire, replies, _ = native_exchange
    sock = NativeReplies(wire, replies)
    monkeypatch.setattr(C.socket, 'create_connection', lambda *a, **kw: sock)
    client = C.RfStatusClient()
    with client.headroom_lease():
        client.headroom_capability()
        client.headroom_arm('s3cret')
        client.headroom_begin(H.HeadroomPlan().begin_args(SID, cable_confirmed=True))
        client.headroom_abort({'study': H.STUDY, 'session_id': SID})
        closing = client.headroom_closing_readback()
        assert closing['confirmed'] is True, closing
        assert H._closing_confirmed(dict(closing, confirmed=True)) is True


def test_inconsistent_pending_operation_cannot_confirm_client_close(native_exchange, monkeypatch):
    wire, replies, _ = native_exchange
    # Native OFFLINE/held-lease reply, with one contradictory pending field.
    replies = json.loads(json.dumps(replies[:4]))
    replies[2]['result']['pending_op'] = 'tx.start'
    sock = NativeReplies(wire[:4], replies)
    monkeypatch.setattr(C.socket, 'create_connection', lambda *a, **kw: sock)
    client = C.RfStatusClient()
    with client.headroom_lease():
        assert client.headroom_closing_readback()['confirmed'] is False


def test_inconsistent_pending_operation_cannot_confirm_controller_close(native_exchange):
    _, replies, _ = native_exchange
    state = dict(replies[10]['result'], pending_op='tx.start')
    closing = dict(confirmed=True, armed=False, state=state,
                   pl=dict(io_ok=True, tx=dict(source_selected=False), rx=dict(armed=False)))
    assert not H._closing_confirmed(closing)


@pytest.mark.parametrize('lease', [False, None, 0, 1, 'true'])
def test_non_boolean_or_absent_connection_lease_is_refused(native_exchange, lease):
    _, replies, _ = native_exchange
    info, state, pl = replies[1]['result'], replies[2]['result'], replies[3]['result']
    from test_rf_headroom_corrections import eligible
    cap = eligible()
    cap['lease_held'] = lease
    assert not H.capability_available(cap)
    state = dict(state, lease_held=lease)
    closing = dict(confirmed=True, armed=False, state=state,
                   pl=dict(io_ok=True, tx=dict(source_selected=False), rx=dict(armed=False)))
    assert not H._closing_confirmed(closing)


@pytest.mark.parametrize('invalid', ['thread', 'generation', 'socket'])
def test_owned_connection_cannot_be_substituted(native_exchange, monkeypatch, invalid):
    wire, replies, _ = native_exchange
    sock = NativeReplies(wire, replies)
    monkeypatch.setattr(C.socket, 'create_connection', lambda *a, **kw: sock)
    client = C.RfStatusClient()
    with client.headroom_lease():
        if invalid == 'thread': client._research_owner = -1
        if invalid == 'generation': client._connection_generation += 1
        if invalid == 'socket': client._session_socket = NativeReplies([], [])
        with pytest.raises(C.StatusProtocolError): client._research_owned()


@pytest.mark.parametrize('operation', ['headroom_capability', 'headroom_closing_readback'])
def test_live_reads_refuse_a_foreign_research_owner(native_exchange, monkeypatch, operation):
    wire, replies, _ = native_exchange
    sock = NativeReplies(wire, replies)
    monkeypatch.setattr(C.socket, 'create_connection', lambda *a, **kw: sock)
    client = C.RfStatusClient()
    with client.headroom_lease():
        client._research_owner = -1
        remaining = len(sock.wire)
        with pytest.raises(C.StatusProtocolError): getattr(client, operation)()
        assert len(sock.wire) == remaining


@pytest.mark.parametrize('operation', ['headroom_capability', 'headroom_closing_readback'])
def test_live_reads_do_not_attest_after_the_owned_generation_changes(native_exchange, monkeypatch, operation):
    wire, replies, _ = native_exchange
    sock = NativeReplies(wire, replies)
    monkeypatch.setattr(C.socket, 'create_connection', lambda *a, **kw: sock)
    client = C.RfStatusClient()
    with client.headroom_lease():
        if operation == 'headroom_closing_readback':
            client.headroom_capability()
            client.headroom_arm('s3cret')
            client.headroom_begin(H.HeadroomPlan().begin_args(SID, cable_confirmed=True))
            client.headroom_abort({'study': H.STUDY, 'session_id': SID})
        request = client._request
        def lose_generation(*args, **kwargs):
            result = request(*args, **kwargs)
            if args[1] == 'pl.status.get': client._connection_generation += 1
            return result
        monkeypatch.setattr(client, '_request', lose_generation)
        with pytest.raises(C.StatusProtocolError): getattr(client, operation)()

