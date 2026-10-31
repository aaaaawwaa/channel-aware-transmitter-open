"""A protocol-level stub daemon that refuses exactly what the real daemon refuses.

Test double for the WIRE, not a simulation of the product: every gate below is a
transcription of the frozen C that decides the same request, with the line it comes
from, and a refusal is the real reply shape
({"id","ok":false,"error":{"code","message"}}, no payload).  Transcribed from:

* rf_overlay/sw/board_app/rf_daemon.c:1643-1709 `op_loopback_physical_capture`, in the
  order the daemon applies them:
    1665  an explicit attenuation_mdB is required
    1668  cable_confirmed must be a boolean
    1671  rf_state_check_physical_loopback (below)
    1673  samples must be 1..max_samples
    1681  ratio must be an explicit 0..4 selection code
    1691  settle_wait_us must be <= RF_SETTLE_WAIT_MAX_US, and observable when > 0
    1706  tx_count == 0 -> "the physical loopback needs a tx.load first"
* rf_overlay/sw/board_app/rf_state.c:105-129 `rf_state_check_physical_loopback`:
    113   a missing request is INVALID_ARGUMENT
    116   cable_confirmed is required
    126   attenuation_mdB must equal RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB
* rf_overlay/sw/board_app/rf_state.h:40
  `#define RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB 46000` -- the teaching/environment
  domain value, pinned to rf_teaching_profile.ATTENUATION_MDB by
  scripts/tests/test_rf_state_loopback_attenuation_pin.py.  The daemon builds its
  refusal message from that same macro, so the shipped binary carries the literal
  "physical loopback requires attenuation_mdB 46000" -- and that literal is the
  authority the entry's daemon probe reads.
* The rule below is READ from that header (`c_gate_attenuation_mdb`), never typed a
  second time.  Until 2026-10-18 this double defaulted to 40000 while the shipping
  domain was 46000 and nothing in the suite compared the two: the C constant moved
  nowhere, and a whole campaign was refused with INVALID_ARGUMENT before it started.
  A copy that can drift is the defect, so the default is now derived.
* rf_daemon.c:1291-1325 op_tx_load is the only place d->tx_count is set; the load
  writes the BRAM and does NOT commit the PL sample-length register
  (rf_core.c:1161-1176 `rf_core_write_tx` vs rf_core.c:1776 `rf_core_configure_tx_path`),
  which is why a loaded-but-never-committed part reads back length_words 0.
* rf_daemon.c:486-499 state.get, 1095-1127 pl.status.get,
  rf_pl_status_reader.c:97-129 the PL snapshot it reports, and
  rf_daemon.c:901-1067 the device.info shape.

The point of the transcription is that a request the product client ACCEPTS can still be
refused here by the same rule the board applies, so a regression test cannot pass by
having a stub that simply says yes.
"""
import json
import re
import socket
import struct
import threading
from pathlib import Path

SETTLE_WAIT_MAX_US = 5000          # rf_daemon.h RF_SETTLE_WAIT_MAX_US
ROOT = Path(__file__).resolve().parents[2]
BOARD_APP = ROOT / 'rf_overlay' / 'sw' / 'board_app'
C_GATE_DEFINE = re.compile(
    r'^#define RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB\s+([0-9]+)u?\s*$', re.MULTILINE)


def c_gate_attenuation_mdb(board_app=None):
    """The attenuation the C gate compares against, read from the C source itself.

    Raises rather than falling back to a remembered number: a double that silently
    substitutes a value is how the gate and the request drifted apart unnoticed.
    """
    path = Path(board_app or BOARD_APP) / 'rf_state.h'
    found = C_GATE_DEFINE.search(path.read_text(encoding='utf-8'))
    if found is None:
        raise RuntimeError('%s carries no RF_PHYSICAL_LOOPBACK_ATTENUATION_MDB' % path)
    return int(found.group(1))


C_GATE_ATTENUATION_MDB = c_gate_attenuation_mdb()
PHYSICAL_OP = 'loopback.physical.capture'
READ_ONLY_OPS = ["protocol.hello", "protocol.close", "device.info", "state.get",
                 "status.subscribe", "pl.status.get"]
CONTROL_OPS = ["control.arm", "control.disarm", "rf.init", "rf.configure", "tx.load",
               "tx.start", "tx.stop", "rx.capture", PHYSICAL_OP]
RELEASE_ID = 'e4dae554182e8c9f5f963c1d5040913a'
SAMPLE_RATE_HZ = 60000000


class LoopbackStubDaemon:
    """Real framing, one retained connection, the real loopback gates."""

    def __init__(self, *, loopback_attenuation_mdb=C_GATE_ATTENUATION_MDB,
                 max_samples=4096,
                 release_id=RELEASE_ID, daemon_version='1.0.4', protocol_minor=6,
                 core=True):
        self.loopback_attenuation_mdb = loopback_attenuation_mdb
        self.max_samples = max_samples
        self.release_id = release_id
        self.daemon_version = daemon_version
        self.protocol_minor = protocol_minor
        self.core = bool(core)
        self.requests = []              # every request received, in order
        self.physical_requests = []     # args of every loopback.physical.capture
        self.refusals = []              # {'op','code','message'} the stub sent
        self.tx_count = 0
        self.tx_wrap = False
        self.armed = False
        self.state = 'READY'
        self.pl_length = 0              # SMP_LEN: 0 until a TX path is committed
        self.pl_source = False
        self.rx_armed = False
        self.rx_done = False
        self.connections = 0
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(4)
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ gates
    def physical_gate(self, args):
        """(code, message) the daemon would answer with, or None to approve."""
        if type(args.get('attenuation_mdB')) is not int:
            return ('INVALID_ARGUMENT',
                    'a physical capture requires an explicit attenuation_mdB')
        if 'cable_confirmed' in args and type(args['cable_confirmed']) is not bool:
            return ('INVALID_ARGUMENT', 'cable_confirmed')
        # rf_state_check_physical_loopback, in its own order.
        if args.get('cable_confirmed') is not True:
            return ('INVALID_ARGUMENT',
                    'physical loopback requires explicit cable confirmation')
        if args['attenuation_mdB'] != self.loopback_attenuation_mdb:
            return ('INVALID_ARGUMENT',
                    'physical loopback requires attenuation_mdB %d'
                    % self.loopback_attenuation_mdb)
        samples = args.get('samples')
        if type(samples) is not int or not 1 <= samples <= self.max_samples:
            return ('INVALID_ARGUMENT', 'samples must be 1..max_samples')
        ratio = args.get('ratio')
        if type(ratio) is not int or not 0 <= ratio <= 4:
            return ('INVALID_ARGUMENT', 'ratio')
        settle = args.get('settle_wait_us', 0)
        if type(settle) is not int or not 0 <= settle <= SETTLE_WAIT_MAX_US:
            return ('INVALID_ARGUMENT', 'settle_wait_us')
        if settle > 0 and args.get('observe_timing') is not True:
            return ('INVALID_ARGUMENT',
                    'a settling wait must be observable: settle_wait_us requires '
                    'observe_timing')
        if self.tx_count == 0:
            return ('INVALID_ARGUMENT',
                    'the physical loopback needs a tx.load first: there is nothing '
                    'to loop back')
        return None

    # ------------------------------------------------------------------ results
    def _info(self):
        return {'daemon': self.daemon_version, 'protocol_major': 1,
                'protocol_minor': self.protocol_minor, 'max_samples': self.max_samples,
                'mode': 'status_only', 'core': self.core, 'armed': self.armed,
                'release_id': self.release_id, 'bit_sha256': 'a' * 64,
                'hwh_sha256': 'b' * 64,
                'capabilities': {'ops': list(READ_ONLY_OPS),
                                 'control_ops': list(CONTROL_OPS),
                                 'headroom': {'study': 'HEADROOM-1',
                                              'protocol_minor': 6}}}

    def _state(self):
        return {'state': self.state, 'pending': False, 'pending_op': None,
                'lease_held': True, 'max_samples': self.max_samples}

    def _pl(self):
        return {'sampled_at_monotonic_ns': 1000, 'io_ok': True,
                'tx': {'source_selected': self.pl_source,
                       'length_words': self.pl_length, 'position_words': 0},
                'rx': {'armed': self.rx_armed, 'done': self.rx_done,
                       'position_words': 0}}

    def ops(self):
        return [request['op'] for request in self.requests]

    # ------------------------------------------------------------------ serving
    def _refuse(self, rid, code, message):
        return {'id': rid, 'ok': False,
                'error': {'code': code, 'message': message}}

    def _handle(self, request, conn, buffer):
        op, rid = request['op'], request['id']
        args = request.get('args') or {}
        self.requests.append({'op': op, 'args': args, 'id': rid})
        if op == 'protocol.hello':
            reply = {'id': rid, 'ok': True,
                     'result': {'daemon': self.daemon_version, 'protocol_major': 1,
                                'protocol_minor': self.protocol_minor,
                                'max_samples': self.max_samples}}
        elif op == 'device.info':
            reply = {'id': rid, 'ok': True, 'result': self._info()}
        elif op == 'state.get':
            reply = {'id': rid, 'ok': True, 'result': self._state()}
        elif op == 'status.subscribe':
            reply = {'id': rid, 'ok': True,
                     'result': dict(self._state(), subscribed=True)}
        elif op == 'pl.status.get':
            reply = {'id': rid, 'ok': True, 'result': self._pl()}
        elif op == 'control.arm':
            if args.get('token') != 'stub-token':
                reply = self._refuse(rid, 'UNAUTHORIZED', 'token refused')
            else:
                self.armed = True
                reply = {'id': rid, 'ok': True, 'result': {'armed': True}}
        elif op == 'control.disarm':
            self.armed = False
            reply = {'id': rid, 'ok': True, 'result': {'armed': False}}
        elif op in ('rf.init', 'rf.configure'):
            reply = {'id': rid, 'ok': True, 'result': {'accepted': True}}
        elif op == 'tx.load':
            count = args.get('bytes')
            if type(count) is not int or count != args.get('samples', 0) * 4:
                reply = self._refuse(rid, 'INVALID_ARGUMENT',
                                     'the tx.load declaration is not usable')
            else:
                buffer = self._read_exact(conn, buffer, count)
                self.tx_count = args['samples']
                self.tx_wrap = bool(args.get('wrap'))
                # rf_core_write_tx writes the BRAM only: the committed sample length
                # register stays where it was until a TX path is configured.
                reply = {'id': rid, 'ok': True,
                         'result': {'samples': args['samples'], 'bytes': count,
                                    'wrap': self.tx_wrap}}
        elif op == PHYSICAL_OP:
            self.physical_requests.append(dict(args))
            refusal = self.physical_gate(args)
            if refusal is not None:
                code, message = refusal
                self.refusals.append({'op': op, 'code': code, 'message': message})
                return self._refuse(rid, code, message), buffer
            # Approved: the real transaction sources the loaded buffer
            # (rf_daemon.c:1715 commits the TX path, i.e. SMP_LEN), runs one capture
            # and releases the TX source again.
            self.pl_length = self.tx_count
            self.pl_source = True
            samples = args['samples']
            header = {'id': rid, 'type': 'iq_frame', 'direction': 'rx',
                      'format': 's16_iq_le', 'samples': samples,
                      'bytes': samples * 4, 'sample_rate_hz': SAMPLE_RATE_HZ,
                      'ratio': args['ratio'], 'timestamp_ns': 123456789}
            payload = b''.join(struct.pack('<hh', (n % 1000) - 500, 500 - (n % 1000))
                               for n in range(samples))
            self.pl_source = False
            self.rx_armed = False
            self.rx_done = True
            return None, buffer, header, payload
        else:
            reply = self._refuse(rid, 'UNSUPPORTED', op)
        return reply, buffer

    def _read_line(self, conn, buffer):
        while b'\n' not in buffer:
            try:
                part = conn.recv(8192)
            except socket.timeout:
                if self._stop.is_set():
                    return None, buffer
                continue
            if not part:
                return None, buffer
            buffer += part
        line, buffer = buffer.split(b'\n', 1)
        return line, buffer

    def _read_exact(self, conn, buffer, count):
        while len(buffer) < count:
            try:
                part = conn.recv(8192)
            except socket.timeout:
                if self._stop.is_set():
                    break
                continue
            if not part:
                break
            buffer += part
        return buffer[count:]

    def _serve(self, conn):
        buffer = b''
        while not self._stop.is_set():
            line, buffer = self._read_line(conn, buffer)
            if line is None:
                return
            request = json.loads(line.decode('utf-8'))
            answer = self._handle(request, conn, buffer)
            if len(answer) == 4:
                _ignored, buffer, header, payload = answer
                conn.sendall((json.dumps(header, separators=(',', ':')) + '\n')
                             .encode('ascii') + payload)
                continue
            reply, buffer = answer
            conn.sendall((json.dumps(reply, separators=(',', ':')) + '\n')
                         .encode('ascii'))

    def _run(self):
        while not self._stop.is_set():
            try:
                conn, _peer = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self.connections += 1
            conn.settimeout(0.1)
            try:
                self._serve(conn)
            except (OSError, ValueError):
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
        try:
            self.listener.close()
        except OSError:
            pass

    def close(self):
        self._stop.set()
        self._thread.join(1)
