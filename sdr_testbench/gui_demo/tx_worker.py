"""GUI child process; reuse the existing finite TX + verified LO-off cleanup.

Cooperative cancellation interrupts the existing timed stream sleep. It never
terminates the process or bypasses GNU Radio stop/wait and radio_test's finally.
"""
import argparse
import json
from pathlib import Path
import sys
import time

from profile_contract import _radio, receipt_state


class InterruptibleTime:
    def __init__(self, stop_file):
        self.stop_file = stop_file

    def __getattr__(self, name):
        return getattr(time, name)

    def sleep(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.stop_file and self.stop_file.exists():
                raise KeyboardInterrupt('GUI requested graceful stop')
            time.sleep(min(0.025, max(0, deadline - time.monotonic())))


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--stop-file', type=Path)
    parser.add_argument('--summary-file', type=Path)
    args, remaining = parser.parse_known_args()
    radio = _radio()
    before = set((radio.ROOT / 'results').glob('*-tx-bpsk/result.json'))
    radio.time = InterruptibleTime(args.stop_file)
    original_graph = radio.run_graph
    def checked_graph(*graph_args, **graph_kwargs):
        if args.stop_file and args.stop_file.exists():
            raise KeyboardInterrupt('GUI canceled before graph creation')
        return original_graph(*graph_args, **graph_kwargs)
    radio.run_graph = checked_graph
    sys.argv = [str(Path(radio.__file__)), *remaining]
    code = 0
    error = None
    try:
        radio.main()
    except BaseException as exc:
        code = int(exc.code or 0) if isinstance(exc, SystemExit) and isinstance(exc.code, int) else 1
        error = type(exc).__name__ + ': ' + str(exc)
    after = set((radio.ROOT / 'results').glob('*-tx-bpsk/result.json')) - before
    result_path = next(iter(after)) if len(after) == 1 else None
    receipt = json.loads(result_path.read_text(encoding='utf-8')) if result_path else {}
    summary = dict(exit_code=code, state=receipt_state(receipt), error=error,
                   result_path=str(result_path) if result_path else None,
                   stop_requested=bool(args.stop_file and args.stop_file.exists()),
                   receipt=receipt)
    if args.summary_file:
        args.summary_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'gui_tx_result': summary}, ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
