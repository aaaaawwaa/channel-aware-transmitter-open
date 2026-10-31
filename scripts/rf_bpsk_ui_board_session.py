"""Finite offscreen UiView acceptance, not a human or GT911 timing measurement.

Controls use the production setters/worker. Captures use actual UiView hit,
press/release, callback and _PanelScreen landing. No Overlay is created, and no
physical LCD is opened. Every returned immutable IQ frame is retained for replay.
"""
import argparse, copy, dataclasses, hashlib, json, pathlib, signal, sys, time

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from rf_bpsk_ui_acceptance import campaign, fourclass_campaign

CAPTURE_DEADLINE = 15.0


def fresh(snapshot):
    stamp = snapshot.get('received_at_monotonic')
    return type(stamp) in (int, float) and 0 <= time.monotonic()-stamp <= 3.0


def safe(s):
    pl = s.get("pl_status") or {}
    return bool(fresh(s) and s.get("online") and not s.get("stale") and s.get("armed") is False
                and pl.get("io_ok") and (pl.get("rx") or {}).get("armed") is False
                and (pl.get("tx") or {}).get("source_selected") is False)


def _jsonable(value):
    """Serialize a worker handoff value without dropping its nested evidence."""
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value


class EvidenceClient:
    """Transparent retained-client observer: preserve the exact validated frame."""
    def __init__(self, client):
        self.client = client
        self.frame = None
        # Offline diagnostic harness only: the product session does not expose
        # settle_wait_us, so the diagnostic runner injects the already-reviewed
        # physical-capture arguments here.  Production files are not changed.
        self.capture_extra = {}

    def __getattr__(self, name):
        return getattr(self.client, name)

    def capture(self, args, physical=False):
        wire = dict(args or {})
        if physical and self.capture_extra:
            for key in ("observe_timing", "settle_wait_us"):
                if key in self.capture_extra:
                    wire[key] = self.capture_extra[key]
        frame = self.client.capture(wire, physical=physical)
        self.frame = frame
        return frame

    def digital_capture(self, args):
        """Thin harness adapter for the existing ``loopback.digital.capture`` op.

        It reuses the frozen client's live-advertised check, connection/session
        lock, binary IQ header/payload validation and I/O lock.  It does not go
        through the JSON ``control_op`` path, does not pretend to be a physical
        capture, and explicitly requires both RX released and TX source off.
        """
        underlying = self.client
        if not getattr(underlying, "_tx_session_ready", False):
            raise RuntimeError("digital capture requires a loaded TX session")
        op = "loopback.digital.capture"
        wire = dict(args or {})
        with underlying._io_lock:  # noqa: SLF001 - deliberate thin harness adapter
            underlying._ordinary_owner()  # noqa: SLF001
            frame = underlying._capture_unlocked(op, wire, False)  # noqa: SLF001
        if frame.op != op:
            raise RuntimeError("digital capture returned wrong op: %r" % (frame.op,))
        if frame.physical is not False:
            raise RuntimeError("digital capture was mislabeled physical")
        if frame.post_readback.rx_armed is not False:
            raise RuntimeError("digital capture did not release RX")
        if frame.post_readback.tx_source_selected is not False:
            raise RuntimeError("digital capture did not close TX source")
        self.frame = frame
        return frame


class PanelEvidenceWorker:
    """Observe the same handoff consumed by the production _PanelScreen."""
    def __init__(self, session):
        self.session = session

    def __getattr__(self, name):
        return getattr(self.session.worker, name)

    def take_frame(self):
        # This uploaded acceptance runner may poll; the production GUI does not.
        # Keep the actual panel landing order while supplying authoritative freshness.
        _, results, running = self.session.worker.take_frame()
        return self.session.client.poll(), results, running

    def take_rx_result(self):
        payload, status = self.session.worker.take_rx_result()
        self.session._last_status = status
        if payload is not None:
            self.session._last_payload = payload
        return payload, status


def qpsk_single_evidence_ok(record):
    """QPSK single-frame completion: structure/identity/evidence, not BPSK BER."""
    if not isinstance(record, dict):
        return False, "malformed_record"
    if record.get("egress_safe") is not True:
        return False, "egress_unsafe"
    if not record.get("capture_id") or not record.get("iq_sha256"):
        return False, "missing_frame_identity"
    result = record.get("result") or {}
    if result.get("envelope_present") is not True:
        return False, "missing_product_demod"
    if result.get("algorithm_version") != "qpsk-rect-snapshot-v1":
        return False, "qpsk_algorithm_version_mismatch"
    if result.get("iq_order") != "QI":
        return False, "qpsk_iq_order_not_qi"
    cfg = result.get("demod_config") or {}
    cfg = cfg.get("config") if isinstance(cfg, dict) else None
    if not isinstance(cfg, dict) or cfg.get("mode") != "QPSK":
        return False, "demod_config_not_qpsk"
    if result.get("valid_symbols") is None:
        return False, "missing_valid_symbols"
    return True, ""


def _demod_fields(envelope):
    """Extract only measured product demod fields; never infer from TX mode."""
    demod = getattr(envelope, "result", None)
    if demod is None:
        return dict(carrier_sync=None, timing_sync=None, reference_sync=False,
                    reason="", valid_symbols=None, bit_errors=None,
                    compared_bits=None, ber=None, algorithm_version=None,
                    iq_order=None, evm_available=False, evm_rms=None)
    submitted = getattr(envelope, "submitted", None)
    config = getattr(submitted, "config", None)
    iq_order = getattr(submitted, "iq_order", None)
    if iq_order is None:
        iq_order = getattr(config, "iq_order", None)
    def env_field(name):
        return getattr(envelope, name, None)
    return dict(
        carrier_sync=getattr(demod, "carrier_sync", None),
        timing_sync=getattr(demod, "timing_sync", None),
        reference_sync=getattr(demod, "reference_sync", None),
        reason=str(getattr(demod, "reason", "") or ""),
        valid_symbols=getattr(demod, "valid_symbols",
                              getattr(demod, "total_symbols", None)),
        alignment_bits=getattr(demod, "alignment_bits", None),
        bit_errors=getattr(demod, "bit_errors", None),
        compared_bits=getattr(demod, "compared_bits", None),
        ber=getattr(demod, "ber", None),
        algorithm_version=(getattr(envelope, "algorithm_version", None)
                           or getattr(demod, "algorithm_version", None)),
        iq_order=iq_order,
        shaping=getattr(demod, "shaping", None),
        group_delay_samples=env_field("group_delay_samples"),
        transition_symbols=env_field("transition_symbols"),
        valid_symbol_start=env_field("valid_symbol_start"),
        valid_symbol_end=env_field("valid_symbol_end"),
        comparison_start_symbol=(env_field("comparison_start_symbol")
                                 if env_field("comparison_start_symbol") is not None
                                 else getattr(demod, "comparison_start_symbol", None)),
        comparison_end_symbol=env_field("comparison_end_symbol"),
        evm_available=getattr(demod, "evm_available", False),
        evm_rms=getattr(demod, "evm_rms", None))


def _tx_source_selected(status):
    if not isinstance(status, dict):
        return None
    if "tx_source_selected" in status:
        return status.get("tx_source_selected")
    pl = status.get("pl_status") or {}
    return (pl.get("tx") or {}).get("source_selected")


CAPTURE_OBSERVATION_FILES = ("rf_capture_observation_forward.py",
                            "rf_capture_observation.py")


def capture_observation_layout(root=None):
    """Check the required remote package layout before any board work.

    The run directory must contain ``scripts/`` with the runner and both
    observation modules; uploading them flat in one directory is refused.
    """
    root = (pathlib.Path(__file__).resolve().parents[1]
            if root is None else pathlib.Path(root))
    package = root / "scripts"
    missing = [name for name in CAPTURE_OBSERVATION_FILES
               if not (package / name).is_file()]
    return dict(package=str(package), missing=missing,
                complete=not missing)


class CaptureObservationUnavailable(RuntimeError):
    """Named refusal: rootcause requires a real in-window observation hook."""


def _capture_observation_support(required=False):
    """Return the reviewed additive capture_observation forwarder.

    The remote package layout is ``<run>/scripts/rf_rrc_real_validation.py``
    plus ``rf_capture_observation_forward.py`` and
    ``rf_capture_observation.py`` in the same package; the parent run directory
    must be on sys.path.  Rootcause requests must fail closed rather than
    silently producing a run without observation.
    """
    layout = capture_observation_layout()
    if not layout["complete"]:
        if required:
            raise CaptureObservationUnavailable(
                'CAPTURE_OBSERVATION_LAYOUT_MISSING: ' + ','.join(layout["missing"]))
        return None
    try:
        from scripts import rf_capture_observation_forward as forward
        if not hasattr(forward, 'wrap_header_capture'):
            raise ImportError('forwarder lacks wrap_header_capture')
        if not hasattr(forward, 'frame_observation_sidecar'):
            raise ImportError('forwarder lacks frame_observation_sidecar')
        return forward
    except Exception as exc:
        if required:
            raise CaptureObservationUnavailable(
                'CAPTURE_OBSERVATION_IMPORT_FAILED: ' + (str(exc) or type(exc).__name__))
        return None


def _rx_engine_ready(dstatus, rx_mode, rx_shaping):
    """Read the product capability for the explicit RX mode; never infer it."""
    if rx_mode == "QAM64":
        return dstatus.get("qam64_available") is True
    if rx_mode == "QAM16":
        return dstatus.get("qam16_available") is True
    if rx_shaping == "RRC":
        return dstatus.get("rrc_available") is True
    if rx_mode == "BPSK":
        return dstatus.get("available") is True
    return dstatus.get("qpsk_available") is True


class LiveUiSession:
    def __init__(self, expected, output, teaching=False, tx_mode="BPSK",
                 seed=None, amplitude=60, session_index=None, run_id=None,
                 rx_mode="BPSK", rx_seed=None,
                 tx_shaping="RECT", rx_shaping="RECT",
                 tx_rolloff="0.35", rx_rolloff="0.35",
                 require_capture_observation=False, capture_deadline=15.0):
        self.expected = expected
        self.output = pathlib.Path(output)
        self.run_id = str(run_id if run_id is not None else self.output.name)
        self._session_index = session_index
        if tx_mode not in ("BPSK", "QPSK", "QAM16", "QAM64"):
            raise ValueError("tx_mode must be BPSK/QPSK/QAM16/QAM64")
        if rx_mode not in ("BPSK", "QPSK", "QAM16", "QAM64"):
            raise ValueError("rx_mode must be BPSK/QPSK/QAM16/QAM64")
        if tx_shaping not in ("RECT", "RRC") or rx_shaping not in ("RECT", "RRC"):
            raise ValueError("tx_shaping/rx_shaping must be RECT or RRC")
        for name, value in (("tx_rolloff", tx_rolloff), ("rx_rolloff", rx_rolloff)):
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise ValueError(name + " must be a numeric rolloff")
            if str(value) != "0.35":
                raise ValueError(name + " must be 0.35 for this frozen domain")
        if seed is not None and (type(seed) is not int or not 1 <= seed <= 127):
            raise ValueError("tx seed must be an integer 1..127")
        if rx_seed is not None and (type(rx_seed) is not int or not 1 <= rx_seed <= 127):
            raise ValueError("rx reference seed must be an integer 1..127")
        if type(amplitude) is not int or not 0 <= amplitude <= 2047:
            raise ValueError("tx amplitude must be an integer 0..2047")
        if (isinstance(capture_deadline, bool)
                or not isinstance(capture_deadline, (int, float))
                or not 0.0 < float(capture_deadline) <= 120.0):
            raise ValueError("capture_deadline must be a number in (0, 120]")
        self._capture_deadline = float(capture_deadline)
        if rx_mode == "QAM16":
            if tx_mode != "QAM16":
                raise ValueError("QAM16 RX requires QAM16 TX frames")
            if tx_shaping != "RECT" or rx_shaping != "RECT":
                raise ValueError("QAM16/RECT requires tx_shaping=rx_shaping=RECT")
            if rx_seed is None:
                raise ValueError("QAM16 requires an explicit RX reference seed")
            if seed is None:
                raise ValueError("QAM16 requires an explicit TX seed")
        if rx_mode == "QAM64":
            if tx_mode != "QAM64":
                raise ValueError("QAM64 RX requires QAM64 TX frames")
            if tx_shaping != "RECT" or rx_shaping != "RECT":
                raise ValueError("QAM64/RECT requires tx_shaping=rx_shaping=RECT")
            if rx_seed is None:
                raise ValueError("QAM64 requires an explicit RX reference seed")
            if seed is None:
                raise ValueError("QAM64 requires an explicit TX seed")
        self._tx_mode = tx_mode
        self._tx_seed = seed
        self._tx_amplitude = amplitude
        self._rx_mode = rx_mode
        self._rx_seed = rx_seed
        self._tx_shaping = tx_shaping
        self._rx_shaping = rx_shaping
        self._tx_rolloff = str(tx_rolloff)
        self._rx_rolloff = str(rx_rolloff)
        self._rx_submission = None
        self._tx_load_records = []
        self._active_load_evidence = None
        self._tx_control_op = None
        self._tx_load_payload_seq = 0
        release = pathlib.Path('/home/xilinx/overlays/awg_merged/current').resolve()
        sys.path.insert(0, str(release))
        import merged_release as R
        verified = R.verify_release(release, require_software=True)
        if verified['release_id'] != expected:
            raise RuntimeError('verified release mismatch; no client created')
        self._teaching = verified.get('software_contract') == 'cnn-bpsk-teaching-v1'
        if teaching and not self._teaching:
            raise RuntimeError('verified teaching contract required; no client created')
        import rf_status_client as RC
        import rf_status_worker as W
        import ui_model as M
        import ui_view as V
        import pynq_lcd_ui as UI
        import pynq_status as P
        self.client = EvidenceClient(RC.RfStatusClient(timeout=2.0))
        self._capture_observation_forward = None
        try:
            self._install_capture_observation(require_capture_observation)
        except Exception:
            try:
                self.client.close()
            except Exception:
                pass
            raise
        self._install_tx_load_recorder()
        self.worker = W.RfStatusWorker(self.client, poll_interval=100,
                                        release_directory=str(release))
        try:
            self.worker.start()
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                dstatus = self.worker.demod_status()
                engine_ready = _rx_engine_ready(dstatus, rx_mode, rx_shaping)
                if (engine_ready and
                        (not self._teaching or self.worker.teaching_status().get('available'))):
                    break
                time.sleep(0.1)
            dstatus = self.worker.demod_status()
            engine_ready = _rx_engine_ready(dstatus, rx_mode, rx_shaping)
            if not engine_ready:
                raise RuntimeError("%s engine not available: %s" % (rx_mode, dstatus))
            if self._teaching and not self.worker.teaching_status().get('available'):
                raise RuntimeError('teaching engine not available')
        except Exception:
            self.worker.stop()
            self.client.close()
            raise
        try:
            self.app = M.UiApp()
            self.app.set_rx_demod_status(self.worker.demod_status())
            self.app.select_device("AD9361")
            # Device-level observation is simulated; RF state comes from live polls.
            self.app.connected = True
            self._panel_configured = False
            self._last_payload = self._last_status = None
            self.ui_events = []
            self._bind_view(V, UI, P)
        except Exception:
            self.worker.stop()
            self.client.close()
            raise

    def set_session_context(self, session_index=None, run_id=None):
        if session_index is not None:
            self._session_index = session_index
        if run_id is not None:
            self.run_id = str(run_id)

    def _install_capture_observation(self, required=False, forward=None):
        """Install the frame-header hook before init/TX; never degrade silently.

        For rootcause the import and the hook installation are both mandatory and
        happen before the worker starts, the initial read-only check, or any TX.
        """
        module = forward if forward is not None else _capture_observation_support(required)
        if module is None:
            if required:
                raise CaptureObservationUnavailable(
                    'CAPTURE_OBSERVATION_IMPORT_FAILED')
            return False
        try:
            module.wrap_header_capture(self.client)
            inner = module.innermost_client(self.client)
            if not getattr(inner, module.HOOK_FLAG, False):
                raise RuntimeError('hook flag not installed')
            self._capture_observation_forward = module
            return True
        except Exception as exc:
            self._capture_observation_forward = None
            if required:
                raise CaptureObservationUnavailable(
                    'CAPTURE_OBSERVATION_HOOK_FAILED: ' +
                    (str(exc) or type(exc).__name__))
            return False

    def _install_tx_load_recorder(self):
        """Observe the exact client tx.load boundary; never adds a call."""
        self._tx_control_op = self.client.control_op

        def recorded_control_op(op, args=None, payload=b""):
            return self._record_control_op(op, args, payload)

        self.client.control_op = recorded_control_op

    def _record_control_op(self, op, args=None, payload=b""):
        if op != "tx.load":
            return self._tx_control_op(op, args, payload=payload)
        actual_payload = bytes(payload)
        self._tx_load_payload_seq += 1
        payload_file = self.output / ("tx-load-%02d.bin" % self._tx_load_payload_seq)
        if payload_file.exists():
            raise FileExistsError("refusing to overwrite tx load payload: %s" % payload_file)
        payload_file.write_bytes(actual_payload)
        record = {
            "session_index": self._session_index,
            "session_id": self.run_id,
            "run_id": self.run_id,
            "tx_mode": self._tx_mode,
            "tx_seed": self._tx_seed,
            "tx_amplitude": self._tx_amplitude,
            "tx_load_metadata": dict(args or {}),
            # Immutable bytes captured at the exact tx.load boundary.  Only the
            # payload is persisted; no token/control argument is written.
            "payload_file": payload_file.name,
            "payload_sha256": hashlib.sha256(actual_payload).hexdigest(),
            "payload_length": len(actual_payload),
            "load_ok": None,
            "response": None,
            "error": None,
        }
        self._tx_load_records.append(record)
        try:
            response = self._tx_control_op(op, dict(args or {}), payload=actual_payload)
        except BaseException as exc:  # noqa: BLE001 - recorded then re-raised
            record["load_ok"] = False
            record["error"] = {"type": type(exc).__name__, "message": str(exc)}
            raise
        record["response"] = response
        record["load_ok"] = not (isinstance(response, dict) and response.get("ok") is False)
        return response

    def _finalize_load_evidence(self, worker_result):
        """Attach worker-level load outcome to the actual tx.load observation."""
        if self._tx_load_records:
            record = self._tx_load_records[-1]
        else:
            record = {
                "session_index": self._session_index,
                "session_id": self.run_id,
                "run_id": self.run_id,
                "tx_mode": self._tx_mode,
                "tx_seed": self._tx_seed,
                "tx_amplitude": self._tx_amplitude,
                "tx_load_metadata": None,
                "payload_sha256": None,
                "payload_length": None,
                "response": None,
                "error": None,
            }
        record["worker_args"] = copy.deepcopy(worker_result.get("args"))
        record["load_ok"] = bool(worker_result.get("ok") is True and record.get("load_ok") is not False)
        record["load_result"] = copy.deepcopy(worker_result.get("result"))
        record["load_code"] = worker_result.get("code")
        record["load_message"] = worker_result.get("message")
        self._active_load_evidence = record
        return record

    def _bind_view(self, V, UI, P):
        import numpy as np
        class MemoryLCD:
            def __init__(self):
                self.array = np.zeros((UI.HEIGHT, UI.WIDTH), dtype=np.uint16)
            def flush(self):
                pass
            def enable(self):
                pass
        self.input_module = UI
        def event(value):
            self.ui_events.append(value)
            P._rx_action(self.app, self.worker, value)
        self.view = V.UiView(MemoryLCD(), self.app, on_rx=event)
        self._rx_page = V.M.PAGE_RX
        self.app.page = self._rx_page
        self.app.rx_subpage = 'overview'
        self.panel = P._PanelScreen(self.view, None, None, None,
                                     rf_worker=PanelEvidenceWorker(self))

    def _tap(self, widget):
        self.view.render()
        if not widget.visible or not widget.enabled:
            raise RuntimeError('capture control is not visible/enabled: %s' % widget.text)
        x0, y0, x1, y1 = widget.box
        self.view.ui.source = self.input_module.ScriptedInput([
            ((x0+x1)//2, (y0+y1)//2, True),
            ((x0+x1)//2, (y0+y1)//2, False)])
        self.panel.input_tick(None)
        self.panel.input_tick(None)

    def panel_tick(self):
        self.panel.render(None)

    def egress_safe(self, snapshot):
        pl = snapshot.get('pl_status') or {}
        return bool(fresh(snapshot) and snapshot.get('online') and not snapshot.get('stale')
                    and snapshot.get('release_id') == self.expected and pl.get('io_ok')
                    and (pl.get('rx') or {}).get('armed') is False
                    and (pl.get('tx') or {}).get('source_selected') is False)

    def _capture_diagnostic_of(self, phase, stage1, refused, status, readback, egress_safe):
        """Raw evidence of one capture attempt, assembled before the dynamic _save().

        This documents a refusal; it must never become one, so anything unreadable is
        reported as diagnostic_error instead of raising.  Before this existed the only
        copy lived in capture()'s local return value, so a frame that never handed over
        was saved with the worker's idle status and no reason at all: the 2026-10-18
        board run's normal-01.json named neither the UI code nor the frame state
        (review D3, evidence/rf-teaching/codex-trainval-diagnostic-2026-10-18).
        """
        try:
            frame = self.client.frame
            payload = self._last_payload if isinstance(self._last_payload, dict) else None
            status = status if isinstance(status, dict) else {}
            refused = refused if isinstance(refused, dict) else {}
            stage1 = stage1 if isinstance(stage1, dict) else {}
            rf_status = self.app.rf_status if isinstance(self.app.rf_status, dict) else {}
            rf_control = self.app.rf_control if isinstance(self.app.rf_control, dict) else {}
            demod = payload.get('demod_result') if payload is not None else None
            return dict(
                phase=phase,
                # the UI's own refusal, raw and unedited
                ui_state=refused.get('state'), ui_code=refused.get('code'),
                ui_reason=refused.get('reason'), ui_capture_id=refused.get('capture_id'),
                ui_physical=refused.get('physical'),
                stage1_state=stage1.get('state'), stage1_code=stage1.get('code'),
                stage1_reason=stage1.get('reason'),
                stage1_pending_action=stage1.get('pending_action'),
                final_state=refused.get('state'), final_code=refused.get('code'),
                final_reason=refused.get('reason'),
                # the worker status this frame was saved with
                worker_state=status.get('state'), worker_code=status.get('code'),
                worker_reason=status.get('reason'),
                worker_is_capturing=bool(self.worker.is_capturing()),
                # did a frame / a handoff payload arrive at all
                frame_present=frame is not None, payload_present=payload is not None,
                frame_capture_id=getattr(frame, 'capture_id', None),
                payload_frame_present=payload is not None and payload.get('frame') is not None,
                payload_demod_present=demod is not None,
                readback=readback, egress_safe=egress_safe,
                daemon_state=rf_status.get("daemon_state"),
                tx_session_ready=rf_status.get("tx_session_ready"),
                armed=rf_status.get("armed"), online=rf_status.get("online"),
                stale=rf_status.get("stale"),
                received_at=rf_status.get("received_at_monotonic"),
                now=time.monotonic(),
                control_online=self.app._rf_control_online,
                control_state=self.app._rf_control_state,
                control_armed=self.app._rf_control_armed,
                rx_capture_capability=self.app.rx_capture_capability()[0],
                rf_control=rf_control.get("pending_action"))
        except Exception as exc:
            return dict(phase=phase,
                        diagnostic_error='%s: %s' % (type(exc).__name__, exc))

    def _save(self, tag, status, readback):
        frame = self.client.frame
        payload = self._last_payload
        summary = self.app.rx_demod_summary()
        meta = dict(capture_status=status, readback=readback, display=summary,
                    ui_events=list(self.ui_events), scope='offscreen UiView; not human GT911')
        forward = getattr(self, '_capture_observation_forward', None)
        if forward is not None and frame is not None:
            try:
                sidecar = forward.frame_observation_sidecar(self.client, frame)
                side_path = forward.write_observation_sidecar(
                    self.output, tag, sidecar)
                meta['capture_observation'] = sidecar
                meta['capture_observation_file'] = side_path.name
            except Exception as exc:
                meta['capture_observation_error'] = (
                    type(exc).__name__ + ': ' + (str(exc) or 'observation failed'))
        # Written with the frame's own artifact, so a failed capture keeps its reason
        # even when the failure never returns to the caller.
        diagnostic = getattr(self, '_capture_diagnostic', None)
        if diagnostic is not None:
            meta['ui_diagnostic'] = diagnostic
        if getattr(self, '_off_tx', None) is not None:
            meta['off_tx'] = dict(self._off_tx)
        if getattr(self, '_capture_tx_state', None) is not None:
            meta['capture_tx_state'] = dict(self._capture_tx_state)
        if getattr(self, '_capture_command_seconds', None) is not None:
            meta['capture_command_seconds'] = self._capture_command_seconds
            meta['capture_command_timing_note'] = 'command closure time, not TX-on time'
            meta['tx_on_measurement'] = 'NOT_MEASURED'
        meta['session_index'] = getattr(self, '_session_index', None)
        meta['tx_shaping'] = getattr(self, '_tx_shaping', None)
        meta['rx_shaping'] = getattr(self, '_rx_shaping', None)
        meta['tx_rolloff'] = getattr(self, '_tx_rolloff', None)
        meta['rx_rolloff'] = getattr(self, '_rx_rolloff', None)
        meta['rx_submission'] = copy.deepcopy(getattr(self, '_rx_submission', None))
        if getattr(self, '_capture_result', None) is not None:
            meta['capture_result'] = copy.deepcopy(self._capture_result)
        meta['session_id'] = getattr(self, 'run_id', None)
        meta['run_id'] = getattr(self, 'run_id', None)
        if getattr(self, '_active_load_evidence', None) is not None:
            meta['tx_load_evidence'] = copy.deepcopy(self._active_load_evidence)
        if getattr(self, '_teaching', False) and hasattr(self.app, 'rx_constellation_summary'):
            meta['display_constellation'] = self.app.rx_constellation_summary()
        if frame is not None:
            meta.update(frame.to_dict())
            meta.update(format='s16_iq_le', iq_sha256=hashlib.sha256(frame.payload).hexdigest())
            meta['pre_readback'] = frame.pre_readback.to_dict()
            meta['post_readback'] = frame.post_readback.to_dict()
            with (self.output/(tag+'.iq')).open('xb') as f:
                f.write(frame.payload)
        if payload is not None:
            meta['handoff_frame'] = payload.get('frame')
            meta['handoff_frame_matches'] = bool(frame is not None and
                                                payload.get('frame') == frame.to_dict())
            keys = ['analysis', 'model_result', 'demod_result']
            if 'constellation' in payload:
                keys.append('constellation')
            # Additive evidence: keep the worker's complete new fields verbatim
            # (including nested frame identity/input domain/BPSK/environment/demod/
            # six-class passthrough).  Never overwrite an existing key.
            for key in ('environment_result', 'modrec4_result'):
                if key in payload and key not in meta:
                    keys.append(key)
            for key in keys:
                value = payload.get(key)
                if value is None:
                    meta[key] = None
                elif key == 'demod_result' and not isinstance(value, dict):
                    meta[key] = dataclasses.asdict(value)
                elif hasattr(value, 'to_dict') and not isinstance(value, dict):
                    meta[key] = value.to_dict()
                elif key in ('environment_result', 'modrec4_result'):
                    meta[key] = _jsonable(value)
                else:
                    meta[key] = value
        with (self.output/(tag+'.json')).open('x', encoding='utf-8') as f:
            json.dump(meta, f, indent=2, allow_nan=False)
        return meta

    def _wait_control(self, action, deadline=25.0):
        """Land worker frames like the panel loop until the action lands."""
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            snapshot, results, running = self.worker.take_frame()
            if snapshot is not None:
                self.app.set_rf_status(snapshot.to_dict())
            for landed_action, result in results:
                self.app.set_rf_control_result(landed_action, result)
                if landed_action == action:
                    return result
            if running is not None:
                self.app.set_rf_control_pending(running)
            time.sleep(0.05)
        raise TimeoutError("control result: " + action)

    def initial(self):
        snapshot = self.client.poll()
        s = snapshot.to_dict()
        self.app.set_rf_status(s)
        return dict(safe=safe(s), release_id=s.get("release_id"))

    def setup(self, seed):
        # 域衰减只有一个出处: 板上 release 里的 rf_teaching_profile(single source),
        # 不在这里再写一个字面量。
        from rf_teaching_profile import ATTENUATION_MDB
        if not self._panel_configured:
            for name, value in (("lo_hz", 80000000), ("sample_rate_hz", 60000000),
                                ("rx_bandwidth_hz", 30000000), ("tx_bandwidth_hz", 30000000),
                                ("attenuation_mdB", ATTENUATION_MDB), ("phy_mode_1r1t", 0),
                                ("idelay_en", 1), ("idelay_tap", 29)):
                why = self.app.set_rf_parameter(name, value)
                if why is not None:
                    raise RuntimeError("set_rf_parameter(%s): %s" % (name, why))
            why = self.app.set_rf_signal_kind("digital")
            if why is not None:
                raise RuntimeError("set_rf_signal_kind: %s" % why)
            tx_shaping = getattr(self, "_tx_shaping", "RECT")
            tx_rolloff = getattr(self, "_tx_rolloff", "0.35")
            rx_shaping = getattr(self, "_rx_shaping", "RECT")
            for name, value in (("mode", self._tx_mode),
                                ("filter", tx_shaping),
                                ("rolloff", tx_rolloff),
                                ("symbol_rate_hz", 3000000),
                                ("amplitude", self._tx_amplitude)):
                why = self.app.set_rf_digital_parameter(name, value)
                if why is not None:
                    raise RuntimeError("set_rf_digital_parameter(%s): %s" % (name, why))
            self._panel_configured = True
        why = self.app.set_rf_digital_parameter(
            "seed", self._tx_seed if self._tx_seed is not None else seed)
        if why is not None:
            raise RuntimeError("set_rf_digital_parameter(seed): %s" % why)
        for action in ("arm", "init", "configure", "load"):
            args, why = self.app.rf_parameter_plan(action)
            if why is not None:
                raise RuntimeError("plan %s: %s" % (action, why))
            queued = self.worker.submit_control(action, "/etc/rf-control.token", args)
            if queued.get("queued") is not True:
                raise RuntimeError("submit %s: %s" % (action, queued))
            result = self._wait_control(action)
            if action == "load":
                self._finalize_load_evidence(result)
            if result.get("ok") is not True:
                raise RuntimeError("control %s failed: %s" % (action, result))
        rx_seed = self._rx_seed if self._rx_seed is not None else seed
        self._rx_seed_effective = rx_seed
        for key, value in (('expected_mode', self._rx_mode), ('symrate_hz', 3000000),
                           ('shaping', getattr(self, "_rx_shaping", "RECT")),
                           ('decim', 1), ('rx_len', 4096),
                           ('input_fs_hz', 60000000), ('reference_enabled', True),
                           ('reference_seed', rx_seed)):
            why = self.app.set_rx_demod_setting(key, value)
            if why is not None:
                raise RuntimeError('RX setting %s: %s' % (key, why))
        # Prove the explicit RX configuration reaches the product demod submission.
        submission = self.app.rx_demod_submission()
        if submission is None:
            raise RuntimeError('product demod submission is None for RX mode %s/%s'
                               % (self._rx_mode, self._rx_shaping))
        self._rx_submission = _jsonable(submission)
        self.panel_tick()

    def capture(self, tag, physical):
        self.app.page = self._rx_page
        self.app.rx_subpage = 'overview'
        self.panel_tick()
        self.client.frame = None
        self._last_payload = self._last_status = None
        self._capture_diagnostic = None
        self._off_tx = None
        self._capture_tx_state = None
        self._capture_started = time.monotonic()
        self.ui_events = []
        stage1 = None
        pre_readback = self.client.poll().to_dict()
        pre_tx = _tx_source_selected(pre_readback)
        off_pre = pre_readback if not physical else None
        off_pre_tx = pre_tx if off_pre is not None else None
        if physical:
            self._tap(self.view.rx_loopback_btn)
            stage1 = dict(self.app.rx_capture or {})
            if stage1.get('pending_action') != 'loopback':
                raise RuntimeError('physical loopback prompt not reached')
            if self.worker.is_capturing() or self.client.frame is not None:
                raise RuntimeError('first loopback hit unexpectedly captured')
            self._tap(self.view.rx_loopback_confirm_btn)
        else:
            # OFF path is ordinary RX capture only; no loopback/TX entry is called.
            self._tap(self.view.rx_capture_btn)
        refused = dict(self.app.rx_capture or {})
        deadline = time.monotonic() + self._capture_deadline
        while time.monotonic() < deadline:
            if not self.worker.is_capturing():
                break
            time.sleep(0.02)
        else:
            self.worker.cancel_capture()
            readback = self.client.poll().to_dict()
            # Diagnostics first: the dynamic _save() below writes them with the frame,
            # so a timed-out capture keeps its own UI/worker reason.
            self._capture_diagnostic = self._capture_diagnostic_of(
                'capture_timeout', stage1, refused, self._last_status, readback, None)
            self._save(tag, dict(state='timeout'), readback)
            raise TimeoutError("capture deadline")
        self.panel_tick()
        payload, status = self._last_payload, self._last_status
        result = dict(carrier_sync=None, timing_sync=None, reference_sync=False,
                      valid_symbols=None, bit_errors=None, compared_bits=None,
                      ber=None, algorithm_version=None, iq_order=None,
                      evm_available=False, evm_rms=None,
                      reason=(status or {}).get("state", ""))
        state = (status or {}).get("state")
        if state == "done" and payload is not None and payload.get("demod_result") is not None:
            envelope = payload["demod_result"]
            result = _demod_fields(envelope)
            result["tx_mode"] = self._tx_mode
            result["tx_seed"] = self._tx_seed
            result["tx_shaping"] = getattr(self, "_tx_shaping", "RECT")
            result["tx_rolloff"] = getattr(self, "_tx_rolloff", "0.35")
            result["rx_mode"] = self._rx_mode
            result["rx_seed"] = getattr(self, "_rx_seed_effective", self._rx_seed)
            result["rx_shaping"] = getattr(self, "_rx_shaping", "RECT")
            result["rx_rolloff"] = getattr(self, "_rx_rolloff", "0.35")
            try:
                result["demod_config"] = _jsonable(self.app.rx_demod_submission())
            except Exception:
                result["demod_config"] = None
            summary = self.app.rx_demod_summary()
            result["display_ber"] = summary.get("ber")
            result["display_reference_sync"] = summary.get("reference_sync")
            result["display_current"] = summary.get("current")
            result["envelope_present"] = self.app.rx_demod_result is not None
        readback = self.client.poll().to_dict()
        egress_safe = self.egress_safe(readback)
        post_tx = _tx_source_selected(readback)
        off_post_tx = post_tx if not physical else None
        off_tx_ok = None if physical else bool(off_pre_tx is False and off_post_tx is False)
        if not physical:
            self._off_tx = dict(pre_tx_source_selected=off_pre_tx,
                                post_tx_source_selected=off_post_tx, ok=off_tx_ok)
            result["off_tx_ok"] = off_tx_ok
            result["off_tx"] = self._off_tx
        result["tx_shaping"] = getattr(self, "_tx_shaping", "RECT")
        result["tx_rolloff"] = getattr(self, "_tx_rolloff", "0.35")
        result["rx_shaping"] = getattr(self, "_rx_shaping", "RECT")
        result["rx_rolloff"] = getattr(self, "_rx_rolloff", "0.35")
        result["tx_mode"] = self._tx_mode
        result["tx_seed"] = self._tx_seed
        result["rx_mode"] = self._rx_mode
        result["rx_seed"] = getattr(self, "_rx_seed_effective", self._rx_seed)
        result["rx_submission"] = copy.deepcopy(getattr(self, "_rx_submission", None))
        result["pre_tx_source_selected"] = pre_tx
        result["post_tx_source_selected"] = post_tx
        if not egress_safe:
            result["reason"] = "EGRESS_UNSAFE"
        # Assembled BEFORE the dynamic _save(): the same dict goes into the frame's own
        # artifact and into the returned record, so neither copy can lose the refusal.
        self._capture_diagnostic = self._capture_diagnostic_of(
            'capture', stage1, refused, status, readback, egress_safe)
        result["ui_diagnostic"] = self._capture_diagnostic
        self._capture_tx_state = dict(pre_tx_source_selected=pre_tx,
                                      post_tx_source_selected=post_tx,
                                      off_tx_ok=off_tx_ok)
        self._capture_command_seconds = time.monotonic() - self._capture_started
        self._capture_result = copy.deepcopy(result)
        meta = self._save(tag, status, readback)
        result["capture_observation"] = meta.get('capture_observation')
        result["capture_observation_file"] = meta.get('capture_observation_file')
        if meta.get('capture_observation_error') is not None:
            result["capture_observation_error"] = meta['capture_observation_error']
        if getattr(self,'_teaching',False) and not physical and (meta.get('constellation') or {}).get('points'):
            raise RuntimeError('OFF false constellation; evidence saved '+tag)
        if not physical and not off_tx_ok:
            raise RuntimeError('OFF TX source selected; evidence saved '+tag)
        if (state != 'done' or payload is None or self.client.frame is None
                or meta.get('handoff_frame_matches') is not True
                or payload.get('demod_result') is None
                or (getattr(self, '_teaching', False) and payload.get('constellation') is None)):
            raise RuntimeError('incomplete capture: status/frame/demod evidence missing; saved '+tag)
        return dict(on=physical, result=result, egress_safe=egress_safe,
                    modrec4=meta.get('modrec4_result'),
                    environment=meta.get('environment_result'),
                    six_class=meta.get('model_result'), off_tx_ok=off_tx_ok,
                    off_tx=dict(self._off_tx) if self._off_tx is not None else None,
                    pre_tx_source_selected=pre_tx,
                    post_tx_source_selected=post_tx,
                    tx_load_evidence=copy.deepcopy(getattr(self, '_active_load_evidence', None)),
                    capture_id=meta.get('capture_id'), release_id=meta.get('release_id'),
                    request_id=meta.get('request_id'),
                    daemon_timestamp_ns=meta.get('daemon_timestamp_ns'),
                    iq_sha256=meta.get('iq_sha256'), iq_file=tag+'.iq',
                    metadata_file=tag+'.json', ui_events=list(self.ui_events),
                    capture_observation=meta.get('capture_observation'),
                    capture_observation_file=meta.get('capture_observation_file'))

    def cleanup(self):
        try:
            self.worker.stop()
        except Exception:
            pass
        ack = False
        try:
            result = self.client.control_disarm()
            ack = isinstance(result, dict) and result.get('armed') is False
        except Exception:
            pass
        s = self.client.poll().to_dict()
        return dict(safe=safe(s) and s.get('release_id') == self.expected, disarm_ack=ack)

    def close(self):
        # Also stop unowned sessions refused by initial(); never disarm those.
        try:
            self.worker.stop()
        except Exception:
            pass
        try:
            self.client.close()
        except Exception:
            pass


_CANCEL_SIGNAL = None


def _cancel_handler(signum, frame):
    """Turn SIGTERM/SIGINT into KeyboardInterrupt so campaign finally/cleanup runs."""
    global _CANCEL_SIGNAL
    _CANCEL_SIGNAL = signum
    raise KeyboardInterrupt("cancelled by signal %d" % signum)


def main(argv=None):
    global _CANCEL_SIGNAL
    signal.signal(signal.SIGTERM, _cancel_handler)
    signal.signal(signal.SIGINT, _cancel_handler)
    p = argparse.ArgumentParser()
    p.add_argument("--expected-release", required=True)
    p.add_argument("--output", required=True)
    p.add_argument('--cable-confirmed', action='store_true')
    p.add_argument('--teaching',action='store_true',help='require the attested S1 teaching contract')
    p.add_argument('--single-frame', action='store_true',
                   help='one session/init/TX/one physical frame; default 33-frame campaign unchanged')
    p.add_argument('--mode', choices=('BPSK', 'QPSK', 'QAM16', 'QAM64'), default='BPSK',
                   help='TX digital mode (BPSK default)')
    p.add_argument('--seed', type=int, default=None, help='TX PRBS7 seed 1..127')
    p.add_argument('--rx-mode', choices=('BPSK', 'QPSK', 'QAM16', 'QAM64'), default='BPSK',
                   help='RX demod mode (default BPSK; QAM16 requires RECT/QAM16)')
    p.add_argument('--rx-seed', type=int, default=None,
                   help='RX PRBS7 reference seed 1..127 (default: --seed)')
    p.add_argument('--tx-shaping', choices=('RECT', 'RRC'), default='RECT',
                   help='TX pulse shaping (default RECT)')
    p.add_argument('--rx-shaping', choices=('RECT', 'RRC'), default='RECT',
                   help='RX demod shaping (default RECT; RRC is independent of TX)')
    p.add_argument('--tx-rolloff', default='0.35', help='TX rolloff, frozen to 0.35')
    p.add_argument('--rx-rolloff', default='0.35', help='RX rolloff, frozen to 0.35')
    p.add_argument('--amplitude', type=int, default=60, help='TX component peak 0..2047 (default 60)')
    p.add_argument('--off', action='store_true',
                   help='single OFF capture: ordinary rx.capture, no loopback/TX')
    p.add_argument('--fourclass', action='store_true',
                   help='4 sessions / 14 frames: BPSK off+3, QPSK3, QAM16 3, QAM64 3+off')
    args = p.parse_args(argv)
    if not args.cable_confirmed:
        raise SystemExit('REFUSE: --cable-confirmed attestation required for TX1-RX1 cable')
    if args.seed is not None and not 1 <= args.seed <= 127:
        raise SystemExit('REFUSE: --seed must be 1..127')
    if args.rx_seed is not None and not 1 <= args.rx_seed <= 127:
        raise SystemExit('REFUSE: --rx-seed must be 1..127')
    rx_seed = args.rx_seed if args.rx_seed is not None else args.seed
    if str(args.tx_rolloff) != '0.35' or str(args.rx_rolloff) != '0.35':
        raise SystemExit('REFUSE: RRC rolloff is frozen to 0.35')
    if not 0 <= args.amplitude <= 2047:
        raise SystemExit('REFUSE: --amplitude must be 0..2047')
    if args.off and not args.single_frame:
        raise SystemExit('REFUSE: --off requires --single-frame')
    if args.fourclass and (args.single_frame or args.off):
        raise SystemExit('REFUSE: --fourclass cannot be combined with --single-frame/--off')
    if args.fourclass and args.amplitude != 60:
        raise SystemExit('REFUSE: --fourclass truth/config requires --amplitude 60')
    output = pathlib.Path(args.output)
    if output.exists() and any(output.iterdir()):
        raise SystemExit('REFUSE: output must be absent or empty')
    output.mkdir(parents=True, exist_ok=True)
    try:
        if args.fourclass:
            factory = lambda mode, seed, amp: LiveUiSession(
                args.expected_release, output, teaching=args.teaching,
                tx_mode=mode, seed=seed, amplitude=amp,
                rx_mode=args.rx_mode, rx_seed=rx_seed,
                tx_shaping=args.tx_shaping, rx_shaping=args.rx_shaping,
                tx_rolloff=args.tx_rolloff, rx_rolloff=args.rx_rolloff)
            result = fourclass_campaign(factory, output, args.expected_release,
                                        amplitude=args.amplitude)
        else:
            factory = lambda: LiveUiSession(
                args.expected_release, output, teaching=args.teaching,
                tx_mode=args.mode, seed=args.seed, amplitude=args.amplitude,
                rx_mode=args.rx_mode, rx_seed=rx_seed,
                tx_shaping=args.tx_shaping, rx_shaping=args.rx_shaping,
                tx_rolloff=args.tx_rolloff, rx_rolloff=args.rx_rolloff)
            result = campaign(factory, output, args.expected_release,
                              single=args.single_frame, seed=args.seed,
                              physical=(False if args.off else True),
                              require_ber=(args.rx_mode == 'BPSK'))
            if args.single_frame and args.rx_mode == 'QPSK' and not args.off:
                record = (result.get('records') or [{}])[0]
                ok, problem = qpsk_single_evidence_ok(record)
                result['qpsk_evidence_ok'] = ok
                result['qpsk_evidence_problem'] = problem
                # QPSK is NOT graded by BPSK sync/BER or by a CNN class; only
                # the measured product demod structure/identity above.
                result['passed'] = bool(ok and result.get('stop_cause') is None)
    except KeyboardInterrupt:
        # The campaign's finally has already called cleanup()/close() on the owned
        # session.  Exit nonzero so the shell does an independent readback.
        print(json.dumps({"cancelled": True, "signal": _CANCEL_SIGNAL,
                          "note": "campaign finally cleanup executed"}))
        return 128 + (_CANCEL_SIGNAL or signal.SIGINT)
    (output / "final.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({k: result.get(k) for k in ("passed", "stop_cause", "on_aligned",
                                                  "off_false_ber", "aggregate_ber",
                                                  "single_frame", "fourclass", "counts")}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
