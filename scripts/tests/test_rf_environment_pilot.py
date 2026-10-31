from scripts import rf_environment_pilot as P


class Session:
    def __init__(self, events, unsafe=False):
        self.events = events
        self.unsafe = unsafe
    def initial(self): return {'safe': not self.unsafe, 'release_id': 'release'}
    def setup(self, seed): self.events.append(('setup', seed))
    def scene(self, kind): self.events.append(('scene', kind))
    def domain(self, outside): self.events.append(('domain', outside))
    def capture(self, tag, physical):
        self.events.append(('capture', physical))
        kind = tag.split('-')[1]
        label = {'normal': 'CLEAR', 'narrowband': 'NARROWBAND', 'pulse': 'PULSE',
                 'compound': 'UNKNOWN', 'off': 'UNKNOWN', 'domain': 'INVALID'}[kind]
        return dict(release_id='release', capture_id=tag, iq_sha256=tag,
                    egress_safe=True, environment={'label': label, 'reason': ''})
    def cleanup(self):
        self.events.append(('cleanup',))
        return {'safe': True, 'disarm_ack': True}
    def close(self): self.events.append(('close',))


def test_pilot_keeps_18_new_records_and_safe_session_closures(tmp_path):
    events = []
    result = P.campaign(lambda: Session(events), tmp_path, 'release')
    assert result['passed'] and len(result['records']) == 18
    assert result['split'] == 'development-pilot'
    assert result['formal_physical'] == 'NOT_RUN'
    assert events.count(('cleanup',)) == 3
    assert events.count(('capture', False)) == 3


def test_initial_unsafe_refuses_and_does_not_disarm_other_owner(tmp_path):
    events = []
    result = P.campaign(lambda: Session(events, unsafe=True), tmp_path, 'release')
    assert not result['passed'] and result['records'] == []
    assert events == [('close',)]


def test_capture_exception_cleans_and_stops_no_retry(tmp_path):
    events = []
    class Failed(Session):
        def capture(self, tag, physical): raise TimeoutError('failed capture')
    result = P.campaign(lambda: Failed(events), tmp_path, 'release')
    assert not result['passed'] and len(result['sessions']) == 1
    assert events[-2:] == [('cleanup',), ('close',)]


def test_false_clear_negative_preserved_but_fails_pilot(tmp_path):
    class FalseClear(Session):
        def capture(self, tag, physical):
            row = super().capture(tag, physical)
            if '-compound-' in tag: row['environment']['label'] = 'CLEAR'
            return row
    result = P.campaign(lambda: FalseClear([]), tmp_path, 'release')
    assert not result['passed'] and result['negative_false_clear'] == 3
    assert len(result['records']) == 18


def test_factory_error_and_unconfirmed_close_stop_flow(tmp_path):
    def fail(): raise RuntimeError('engine missing')
    result = P.campaign(fail, tmp_path / 'factory', 'release')
    assert not result['passed'] and result['sessions'][0]['owned'] is False
    class NotClosed(Session):
        def cleanup(self): return {'safe': True, 'disarm_ack': False}
    result = P.campaign(lambda: NotClosed([]), tmp_path / 'close', 'release')
    assert result['stop_cause'] == 'close_unconfirmed' and len(result['records']) == 6


def test_unsafe_egress_is_retained_and_prevents_next_capture(tmp_path):
    events = []
    class Unsafe(Session):
        def capture(self, tag, physical):
            row = super().capture(tag, physical)
            row['egress_safe'] = False
            return row
    result = P.campaign(lambda: Unsafe(events), tmp_path, 'release')
    assert not result['passed'] and len(result['records']) == 1
    assert events.count(('capture', True)) == 1
    assert events[-2:] == [('cleanup',), ('close',)]


def test_missing_environment_keeps_failure_and_cleanup_receipt(tmp_path):
    events = []
    class Missing(Session):
        def capture(self, tag, physical):
            row = super().capture(tag, physical)
            row['environment'] = None
            return row
    result = P.campaign(lambda: Missing(events), tmp_path, 'release')
    assert result['stop_cause'] == 'unsafe_or_incomplete_capture'
    assert not result['passed'] and len(result['records']) == 1
    assert result['sessions'][0]['disarm_ack'] is True
    assert (tmp_path / 'result.json').exists()
