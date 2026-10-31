"""Real GNU Radio scheduler check. No IIO/Pluto/board objects are created."""
import json
import os
from pathlib import Path

from profile_contract import HERE, load_profile, preview_samples, preview_symbols

os.environ.setdefault('XDG_CACHE_HOME', str(HERE / 'generated/cache'))
from gnuradio import blocks, gr
import numpy as np
import yaml


def main():
    config = load_profile()
    fg = yaml.safe_load((HERE / 'bpsk_preview.grc').read_text(encoding='utf-8'))
    hardware = next(b for b in fg['blocks'] if b['id'] == 'iio_pluto_sink')
    assert hardware['states']['state'] == 'disabled'
    # Match the actual GRC operators instead of only comparing Python helpers.
    graph = gr.top_block('Offline PRBS - Repeat - quantized amplitude check')
    source = blocks.vector_source_c(preview_symbols(1), False)
    repeat = blocks.repeat(gr.sizeof_gr_complex, 2)
    gain = blocks.multiply_const_cc(round(config['amplitude'] * 2047) / 2047.0)
    sink = blocks.vector_sink_c()
    graph.connect(source, repeat, gain, sink)
    graph.run()
    actual = np.asarray(sink.data())
    expected = np.asarray(preview_samples(config))
    np.testing.assert_allclose(actual, expected, rtol=1e-7, atol=1e-8)
    result = dict(hardware_accessed=False, gnuradio_version=gr.version(),
                  grc_hardware_sink_disabled=True, samples=len(actual),
                  max_sample_error=float(np.max(np.abs(actual - expected))),
                  matches_existing_bounded_sender=True)
    print(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    main()
