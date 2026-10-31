"""Strict readback parsing shared by the preflight and the executors.

libiio returns gain attributes as text such as ``-89.750000 dB``; a JSON
receipt may carry the same text or a bare number.  Both are accepted, and
everything else -- a wrong unit, a missing value, a non-finite value, extra
tokens, a boolean -- is refused, because a readback that cannot be compared
exactly is not a gate.

The two field positions below are not interchangeable and are the reason this
module exists:

* TX LO channel `altvoltage1_out` owns `powerdown`;
* TX chain channel `voltage0_out` owns `hardwaregain`.

Reading the gain from the LO channel rejects a correct safe-off (the real
2026-10-19 17:31:54 TX receipt is the regression fixture).
"""
from __future__ import annotations

import math
import re

#: The only unit suffix accepted.  libiio prints dB gain exactly like this.
DB_UNIT = 'dB'

#: A signed decimal number, optionally with an exponent.  Deliberately not
#: `float()`: that also accepts 'nan', 'inf', '1_0' and '0x10'.
_NUMBER_RE = re.compile(r'^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$')


class ReadbackError(ValueError):
    """A readback that cannot be compared with the required value."""


def parse_db_value(value, label='gain'):
    """Return a finite float from a libiio dB text or a JSON number.

    Accepted:  `-89.750000 dB`, `-60.000000 dB`, `0 dB`, `-12.5`, `-89.75`, 60.
    Refused:   `None`, `''`, `'nan dB'`, `'inf'`, `'-89.75 dBm'`, `'-89.75 db'`,
               `'-89.75 dB extra'`, booleans and any other type.
    """
    if value is None:
        raise ReadbackError('%s is missing (None)' % label)
    if isinstance(value, bool):
        raise ReadbackError('%s is a boolean, not a dB value: %r' % (label, value))
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            raise ReadbackError('%s is not finite: %r' % (label, value))
        return number
    if not isinstance(value, str):
        raise ReadbackError('%s is %s, not a number or text: %r'
                            % (label, type(value).__name__, value))
    text = value.strip()
    if not text:
        raise ReadbackError('%s is empty' % label)
    parts = text.split()
    if len(parts) == 1:
        number = parts[0]
    elif len(parts) == 2:
        if parts[1] != DB_UNIT:
            raise ReadbackError("%s has an unexpected unit %r (only '%s' is accepted)"
                                % (label, parts[1], DB_UNIT))
        number = parts[0]
    else:
        raise ReadbackError('%s is not a single value: %r' % (label, value))
    if not _NUMBER_RE.match(number):
        raise ReadbackError('%s is not a finite decimal number: %r' % (label, value))
    parsed = float(number)
    if not math.isfinite(parsed):
        raise ReadbackError('%s is not finite: %r' % (label, value))
    return parsed


def require_db(value, expected_db, label='gain', tolerance_db=1e-6):
    """Parse `value` and insist it equals `expected_db` within tolerance."""
    parsed = parse_db_value(value, label)
    if expected_db is None:
        return parsed
    if abs(parsed - float(expected_db)) > float(tolerance_db):
        raise ReadbackError('%s is %.6f dB, not %.6f dB (raw %r)'
                            % (label, parsed, float(expected_db), value))
    return parsed


def parse_powerdown(value, label='powerdown'):
    """Return '0' or '1'; anything else is refused, including True/False."""
    if value is None:
        raise ReadbackError('%s is missing (None)' % label)
    if isinstance(value, bool) or not isinstance(value, str):
        raise ReadbackError('%s must be the text "0" or "1"; got %r' % (label, value))
    text = value.strip()
    if text not in ('0', '1'):
        raise ReadbackError('%s must be the text "0" or "1"; got %r' % (label, value))
    return text


def _channels(after, where):
    if not isinstance(after, dict):
        raise ReadbackError('%s has no after snapshot' % where)
    channels = after.get('channels')
    if not isinstance(channels, dict):
        raise ReadbackError('%s has no channels in the after snapshot' % where)
    return channels


def pluto_post_readback(result, expected_gain_db=-89.75,
                        expected_powerdown='1', tolerance_db=1e-6):
    """Gate a radio_test receipt's stop state, using the real field positions.

    `powerdown` comes from `after.channels.altvoltage1_out` (TX LO) and
    `hardwaregain` from `after.channels.voltage0_out` (TX chain).  A receipt
    that carries only one of the two, or carries them on the other channel, is
    refused rather than guessed at.
    """
    if not isinstance(result, dict):
        raise ReadbackError('radio_test result is not an object')
    channels = _channels(result.get('after'), 'radio_test result')
    lo = channels.get('altvoltage1_out')
    tx = channels.get('voltage0_out')
    if not isinstance(lo, dict):
        raise ReadbackError('after.channels.altvoltage1_out is missing (TX LO '
                            'powerdown lives there)')
    if not isinstance(tx, dict):
        raise ReadbackError('after.channels.voltage0_out is missing (TX chain '
                            'hardwaregain lives there)')
    powerdown = parse_powerdown(lo.get('powerdown'),
                                'after.channels.altvoltage1_out.powerdown')
    if powerdown != expected_powerdown:
        raise ReadbackError('after.channels.altvoltage1_out.powerdown is %r, not %r'
                            % (powerdown, expected_powerdown))
    raw_gain = tx.get('hardwaregain')
    gain_db = require_db(raw_gain, expected_gain_db,
                         'after.channels.voltage0_out.hardwaregain', tolerance_db)
    return {'tx_powerdown': powerdown,
            'hardwaregain_db': gain_db,
            'hardwaregain_raw': raw_gain,
            'tx_lo_frequency_hz': lo.get('frequency'),
            'tx_rf_bandwidth_hz': tx.get('rf_bandwidth')}


def board_idle_gate(status):
    """Judge the board idle state; return (ok, problems, detail).

    Every condition is evaluated separately so a failure names the field that
    failed.  `None` is not false: an unknown TX/RX state fails the gate.
    """
    pl = getattr(status, 'pl_status', None)
    if pl is None:
        return False, ['board status has no PL section'], {}
    tx = getattr(pl, 'tx', None)
    rx = getattr(pl, 'rx', None)
    detail = {
        'online': status.online is True,
        'stale': status.stale is True,
        'io_ok': getattr(pl, 'io_ok', None),
        'tx_source_selected': getattr(tx, 'source_selected', None),
        'rx_armed': getattr(rx, 'armed', None),
        'release_id': getattr(status, 'release_id', None),
        'error': getattr(status, 'error', None),
    }
    problems = []
    if detail['online'] is not True:
        problems.append('board is not online')
    if detail['stale'] is not False:
        problems.append('board status is stale')
    if detail['io_ok'] is not True:
        problems.append('PL io_ok is not true (got %r)' % (detail['io_ok'],))
    if detail['tx_source_selected'] is not False:
        problems.append('board TX source is not confirmed off (got %r)'
                        % (detail['tx_source_selected'],))
    if detail['rx_armed'] is not False:
        problems.append('board RX is not confirmed disarmed (got %r)'
                        % (detail['rx_armed'],))
    return (not problems), problems, detail
