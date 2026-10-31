"""Offline checks for the USER/DDR scope verification script."""
import importlib.util
from pathlib import Path

import numpy as np

from siggen.digital_mod import prbs7_bits

SCRIPT = Path(__file__).parents[2] / "scripts" / "scope_verify_user.py"
spec = importlib.util.spec_from_file_location("scope_verify_user", SCRIPT)
scope_verify_user = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope_verify_user)


def test_make_payload_is_deterministic_and_length_exact():
    assert scope_verify_user.make_payload(32) == scope_verify_user.make_payload(32)
    assert len(scope_verify_user.make_payload(1000)) == 1000
    assert scope_verify_user.make_payload(0) == b""


def test_make_payload_is_not_internal_prbs7():
    # 周期 127 的 PRBS7 payload 与固件内部序列不可区分, 无法证明播放的是上传数据。
    prbs = np.packbits(prbs7_bits(32 * 8)).tobytes()
    assert scope_verify_user.make_payload(32) != prbs


def test_selftest_reference_chain_passes():
    assert scope_verify_user._selftest() == 0
