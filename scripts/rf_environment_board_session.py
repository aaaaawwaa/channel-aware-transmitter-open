"""S2 physical development pilot through installed worker/UiView on memory LCD.

Never constructs an Overlay or opens GT911. Scene load uses the real two-hit
confirmation path; each capture retains original IQ, handoff, display and record.
"""
import argparse
import json
from pathlib import Path
import time

from rf_bpsk_ui_board_session import LiveUiSession
from rf_environment_pilot import campaign


class EnvironmentSession(LiveUiSession):
    def __init__(self, expected, output):
        super().__init__(expected, output)
        try:
            import merged_release as R
            release = Path('/home/xilinx/overlays/awg_merged/current').resolve()
            if R.verify_release(release, require_software=True)['software_contract'] != 'cnn-bpsk-environment-v1':
                raise RuntimeError('S2 contract required')
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if self.worker.environment_status().get('available') and self.worker.teaching_status().get('available'):
                    break
                time.sleep(.1)
            else: raise RuntimeError('qualified S2 engines unavailable')
            self._teaching = True
            import pynq_status as P
            self.view._on_rf_control = lambda action: P._rf_control_action(self.app, self.worker, action)
            self.panel_tick()
        except Exception:
            self.close()
            raise

    def scene(self, kind):
        # The button order is the product's own declared scene range, read off the
        # view at runtime (ui_model.ENVIRONMENT_SCENES -> UiView.environment_scene_kinds);
        # a second copy of the list here is what let the OFF slot out of the entry.
        model = getattr(self.view, 'M', None)
        kinds = tuple(getattr(self.view, 'environment_scene_kinds', ())
                      or getattr(model, 'ENVIRONMENT_SCENES', ()))
        if kind not in kinds:
            raise RuntimeError('scene %r is outside the product scene range %r'
                               % (kind, list(kinds)))
        self.app.page, self.app.rx_subpage = self._rx_page, 'environment'
        self.panel_tick()
        self._tap(self.view.environment_scene_btns[kinds.index(kind)])
        if self.app.environment_scene != kind or self.app._rf_signal_draft_kind != 'environment':
            raise RuntimeError('scene draft selection failed')
        self._tap(self.view.environment_load_btn)
        if self.app.rf_control.get('pending_action') != 'load' or self.app._rf_inflight_action is not None:
            raise RuntimeError('first load hit bypassed confirmation')
        self._tap(self.view.environment_load_btn)
        result = self._wait_control('load')
        if result.get('ok') is not True or (result.get('scenario_declaration') or {}).get('truth') != kind:
            raise RuntimeError('scene load failed: ' + str(result))
        self.panel_tick()

    def domain(self, outside):
        why = self.app.set_rx_demod_setting('symrate_hz', 2000000 if outside else 3000000)
        if why is not None: raise RuntimeError('domain setting: ' + why)

    def _save(self, tag, status, readback):
        meta = super()._save(tag, status, readback)
        payload = self._last_payload or {}
        env = payload.get('environment_result')
        record = None
        if env is not None and payload.get('demod_result') is not None and payload.get('constellation') is not None:
            from rf_experiment_record import write_record
            record = write_record(self.output / 'records', self.client.frame, payload['demod_result'],
                payload['constellation'], payload.get('analysis'), payload.get('model_result'),
                rf_config=self.app._rx_record_rf_config, environment=env,
                scenario_declaration=self.app._rx_record_scenario)
        sidecar = dict(environment=env.to_dict() if env else None,
                       display_environment=self.app.rx_environment_summary(),
                       rf_config=dict(self.app._rx_record_rf_config),
                       scenario_declaration=self.app._rx_record_scenario,
                       record=record, iq_sha256=meta.get('iq_sha256'),
                       capture_id=meta.get('capture_id'), release_id=meta.get('release_id'))
        (self.output / (tag + '.environment.json')).write_text(
            json.dumps(sidecar, indent=2, allow_nan=False), encoding='utf-8')
        return meta

    def capture(self, tag, physical):
        row = super().capture(tag, physical)
        extra = json.loads((self.output / (tag + '.environment.json')).read_text(encoding='utf-8'))
        if extra.get('environment') is None or extra['display_environment'].get('current') is not True:
            raise RuntimeError('same-frame environment not landed; evidence retained')
        row.update(environment=extra['environment'], environment_file=tag + '.environment.json',
                   record=extra['record'])
        return row


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--expected-release', required=True)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--cable-confirmed', required=True, action='store_true')
    p.add_argument('--freeze', type=Path, help='complete declaration for finite formal TEST')
    p.add_argument('--offline-rejection', type=Path,
                   help='measured N1-N12 offline negative-control receipt; required with --freeze')
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    factory=lambda: EnvironmentSession(a.expected_release,a.output)
    if a.freeze:
        from rf_environment_acceptance import campaign as formal_campaign, load_offline_rejection
        freeze=json.loads(a.freeze.read_text(encoding='utf-8'))
        if a.offline_rejection is None:
            raise SystemExit('a formal TEST run requires --offline-rejection: the measured '
                             'N1-N12 receipt must be carried by the formal receipt')
        result=formal_campaign(factory,a.output,a.expected_release,freeze,
                               offline_rejection=load_offline_rejection(a.offline_rejection))
        passed=result['formal_rf_passed']
    else:
        result=campaign(factory,a.output,a.expected_release)
        passed=result['passed']
    print(json.dumps({k: v for k, v in result.items() if k not in ('records', 'sessions')}, indent=2))
    raise SystemExit(0 if passed else 1)
