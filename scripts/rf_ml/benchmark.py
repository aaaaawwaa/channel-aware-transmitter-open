"""Standalone pure-numpy hot inference benchmark. Never opens an Overlay.

Copy this script and rf_cnn_runtime.py beside frozen model/IQ files on the
board. CLI runs in a fresh process; baseline RSS is taken before model loading.
"""
import argparse
import hashlib
import json
import os
import platform
import sys
import time
from pathlib import Path

import numpy as np

try:
    from rf_cnn_runtime import load_model
except ImportError:
    sys.path.append(str(Path(__file__).resolve().parents[2] / 'pynq_overlay' / 'sw'))
    from rf_cnn_runtime import load_model


def _rss():
    """Current and process peak RSS in bytes, with no optional dependency."""
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]
        counters = Counters(); counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.windll.kernel32; kernel.GetCurrentProcess.restype = wintypes.HANDLE
        query = ctypes.windll.psapi.GetProcessMemoryInfo
        query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not query(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise OSError('GetProcessMemoryInfo failed')
        return int(counters.WorkingSetSize), int(counters.PeakWorkingSetSize)
    import resource
    current = int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
    return current, int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


def benchmark(model_path, expected_sha256, iq_path, frames=100, warmup_frames=5):
    if type(frames) is not int or frames < 100 or type(warmup_frames) is not int or warmup_frames < 1:
        raise ValueError('benchmark requires at least 100 measured and one warmup frame')
    raw = Path(iq_path).read_bytes()
    if len(raw) != 4096*2*2:
        raise ValueError('benchmark IQ must contain exactly 4096 s16 pairs')
    iq = np.frombuffer(raw, dtype='<i2').reshape(4096, 2)
    baseline, _ = _rss()
    model = load_model(model_path, expected_sha256)
    for _ in range(warmup_frames):
        result = model.predict(iq)
        if result.get('reason') in ('no_energy', 'clipping', 'below_energy_floor', 'unsupported_domain'):
            raise ValueError('benchmark requires valid in-domain signal exercising all layers')
    latencies = []
    max_current = baseline
    for _ in range(frames):
        start = time.perf_counter()
        model.predict(iq)
        latencies.append((time.perf_counter()-start)*1000.0)
        current, peak = _rss()
        max_current = max(max_current, current, peak)
    delta = max(0, max_current - baseline)
    p95 = float(np.percentile(np.asarray(latencies), 95))
    model_path = Path(model_path)
    package_files = [model_path]
    for name in ('rf_model_evaluation.json', 'model_artifact.json'):
        path = model_path.parent / name
        if path.is_file():
            package_files.append(path)
    size = sum(p.stat().st_size for p in package_files)
    return dict(schema_version=1, frames=frames, warmup_frames=warmup_frames,
        hot_p95_ms=p95, hot_median_ms=float(np.median(latencies)), hot_max_ms=max(latencies),
        baseline_rss_bytes=baseline, incremental_peak_rss_bytes=delta,
        rss_method='process_peak_minus_current_baseline_conservative', package_bytes=size,
        model_sha256=expected_sha256, iq_sha256=hashlib.sha256(raw).hexdigest(),
        runtime_sha256=hashlib.sha256(Path(sys.modules[load_model.__module__].__file__).read_bytes()).hexdigest(),
        numpy_version=np.__version__, python_version=platform.python_version(),
        machine=platform.machine(), platform=platform.platform(), model_status=model.status,
        resource_acceptance=bool(p95 <= 200 and delta <= 32*1024*1024 and size <= 128*1024))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True, type=Path)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--iq', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--frames', type=int, default=100)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise FileExistsError('benchmark receipt exists')
    result = benchmark(args.model, args.expected_sha256, args.iq, args.frames)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(result, output, indent=2, allow_nan=False)
    print(json.dumps(result, indent=2))
    return 0 if result['resource_acceptance'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
