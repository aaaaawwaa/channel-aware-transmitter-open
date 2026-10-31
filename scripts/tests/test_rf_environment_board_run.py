import io
from pathlib import Path
from scripts import rf_environment_board_run as B


class Sftp:
    def __init__(self): self.calls = []
    def listdir(self, path):
        if path.endswith('/records'): return ['experiment-abc']
        if '/records/' in path: return ['frame.iq.partial', 'metadata.json', 'receipt.json.partial']
        return ['result.json', 'records', 'failed.iq', 'later.json']
    def get(self, remote, local):
        self.calls.append(remote)
        if remote.endswith('/failed.iq'): raise OSError('failed download')
        Path(local).write_bytes(b'{}')


def test_fetch_keeps_partial_and_continues_after_download_error(tmp_path):
    s = Sftp()
    errors = B.fetch_evidence(s, '/tmp/pilot/out', tmp_path)
    assert errors and (tmp_path / 'later.json').exists()
    assert (tmp_path / 'records/experiment-abc/frame.iq.partial').exists()
    assert len(s.calls) == 6


def test_partial_failed_campaign_can_be_safe_without_being_accepted():
    sessions=[dict(owned=True,closed=True,disarm_ack=True)]*2
    assert B.sessions_closed(sessions)
    assert not B.sessions_closed([])
    assert not B.sessions_closed(sessions+[dict(owned=False,closed=False,disarm_ack=False)])
    assert not B.sessions_closed([dict(owned=True,closed=True,disarm_ack=False)])
