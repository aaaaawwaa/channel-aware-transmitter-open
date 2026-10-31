"""The complete 127-phase/63-bit alignment remains mathematically identical."""
import numpy as np
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import rf_bpsk_receiver as B


def test_all_reference_seeds_and_random_signs_match_scalar_phase_search():
    rng=np.random.RandomState(7041)
    for seed in range(1,128):
        reference=B._reference(seed)
        observed=rng.choice((-1,1),size=B.ALIGNMENT_BITS)
        expected=np.asarray([sum(int(observed[n])*int(reference[(n+phase)%127])
            for n in range(B.ALIGNMENT_BITS))/B.ALIGNMENT_BITS for phase in range(127)])
        actual=B._reference_correlations(observed,reference)
        np.testing.assert_array_equal(actual,expected)
        assert np.array_equal(np.argsort(abs(actual)),np.argsort(abs(expected)))
