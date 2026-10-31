"""Environment capability must be attested; the checkout is not a release."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rf_status_worker as W


def test_environment_is_unavailable_without_verified_release():
    worker = W.RfStatusWorker()
    worker._ensure_environment()
    assert worker.environment_status()['available'] is False
    assert worker._environment_module is None


def test_legacy_release_does_not_try_loading_environment(tmp_path):
    from test_rf_model_release import _release
    directory = tmp_path / 'legacy'
    _release(directory, contract='cnn-bpsk-v1')
    worker = W.RfStatusWorker(release_directory=directory)
    worker._ensure_environment()
    assert worker.environment_status()['available'] is False
    assert worker._environment_baseline is None


def test_new_contract_cannot_load_environment_from_foreign_checkout(tmp_path):
    from test_rf_model_release import _release
    directory = tmp_path / 'new'
    _release(directory, contract='cnn-bpsk-environment-v1')
    worker = W.RfStatusWorker(release_directory=directory)
    worker._ensure_environment()
    assert worker.environment_status()['available'] is False
    assert worker._environment_module is None
