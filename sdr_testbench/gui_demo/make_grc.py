"""Generate an inspectable GRC 3.10 QtGUI preview (no active hardware sink)."""
from pathlib import Path
import yaml

HERE = Path(__file__).resolve().parent


def block(name, kind, x, y, parameters, state='enabled'):
    return dict(name=name, id=kind, parameters=parameters,
                states=dict(coordinate=[x, y], rotation=0, state=state))


def main():
    # GRC evaluates imports before generating Python, with no __file__ binding.
    # Regenerating here after moving the project updates this explicit module path.
    imports = ('import sys, os\nos.environ.setdefault("XDG_CACHE_HOME", '
               + repr(str(HERE / 'generated/cache')) + ')\n'
               'sys.path.insert(0, ' + repr(str(HERE)) + ')\n'
               'from profile_contract import preview_symbols')
    b = [block('demo_imports', 'import', 20, 140, {'imports': imports})]
    for name, value, x in [('samp_rate', '6000000', 20), ('symbol_rate', '3000000', 200),
                           ('sps', 'int(samp_rate / symbol_rate)', 380), ('prbs_seed', '1', 600),
                           ('amplitude', '0.1', 760), ('center_hz', '433900000', 900),
                           ('tx_attenuation_db', '60', 1080)]:
        b.append(block(name, 'variable', x, 40, {'value': value}))
    b += [
        block('prbs7_bpsk_symbols', 'blocks_vector_source_x', 20, 300,
              dict(type='complex', vector='preview_symbols(prbs_seed)', repeat='True', vlen='1', tags='[]')),
        block('rect_pulse_shaping', 'blocks_repeat', 300, 320,
              dict(type='complex', interp='sps', vlen='1')),
        block('tx_amplitude', 'blocks_multiply_const_vxx', 500, 320,
              dict(type='complex', const='round(amplitude * 2047) / 2047.0', vlen='1')),
        block('preview_rate', 'blocks_throttle2', 750, 320,
              dict(type='complex', samples_per_second='samp_rate', vlen='1', ignoretag='True',
                   limit='auto', maximum='0.1')),
        block('iq_time', 'qtgui_time_sink_x', 1020, 250,
              dict(type='complex', name='Local TX I/Q - no RF', size='128', srate='samp_rate',
                   nconnections='1', ymin='-0.14', ymax='0.14', grid='True', autoscale='False',
                   update_time='0.15', gui_hint='0,0,1,1')),
        block('baseband_spectrum', 'qtgui_freq_sink_x', 1020, 450,
              dict(type='complex', name='Local baseband spectrum - not RF measurement',
                   fftsize='4096', wintype='window.WIN_HANN', fc='0', bw='samp_rate',
                   nconnections='1', ymin='-110', ymax='0', grid='True', autoscale='False',
                   update_time='0.15', gui_hint='0,1,1,1')),
        block('bpsk_constellation', 'qtgui_const_sink_x', 1020, 650,
              dict(type='complex', name='Generated BPSK constellation - not RX', size='1024',
                   nconnections='1', xmin='-0.14', xmax='0.14', ymin='-0.14', ymax='0.14',
                   grid='True', autoscale='False', update_time='0.15', gui_hint='1,0,1,2')),
        block('pluto_tx_parameters_DISABLED', 'iio_pluto_sink', 500, 590,
              dict(type='fc32', uri='', frequency='center_hz', samplerate='samp_rate',
                   buffer_size='32768', cyclic='False', attenuation1='tx_attenuation_db',
                   len_tag_key='', filter_source="'Auto'", filter='', fpass='0', fstop='0',
                   bandwidth='1500000'), 'disabled'),
        block('photography_note', 'note', 20, 590,
              dict(note='LOCAL BASEBAND PREVIEW ONLY. Pluto Sink is disabled.\n'
                   'Use bpsk_tx_gui.py for explicit finite RF TX.\n'
                   'Board: pure RX, 60 MSPS, 3 MSym/s, RECT, same PRBS7 seed.')),
    ]
    data = dict(options=dict(parameters=dict(id='bpsk_preview',
                title='BPSK TX BASEBAND PREVIEW - NO RF', author='AC820 external SDR demo',
                description='Software-only preview. Hardware sink is disabled. Use bounded TX GUI for RF.',
                generate_options='qt_gui', output_language='python', run_options='prompt',
                catch_exceptions='True'), states=dict(coordinate=[20, 210], rotation=0, state='enabled')),
                blocks=b, connections=[
                    ['prbs7_bpsk_symbols', '0', 'rect_pulse_shaping', '0'],
                    ['rect_pulse_shaping', '0', 'tx_amplitude', '0'],
                    ['tx_amplitude', '0', 'preview_rate', '0'],
                    ['preview_rate', '0', 'iq_time', '0'],
                    ['preview_rate', '0', 'baseband_spectrum', '0'],
                    ['preview_rate', '0', 'bpsk_constellation', '0'],
                    ['tx_amplitude', '0', 'pluto_tx_parameters_DISABLED', '0'],
                ], metadata=dict(file_format=1, grc_version='3.10.12.0'))
    path = HERE / 'bpsk_preview.grc'
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding='utf-8')
    print(path)


if __name__ == '__main__':
    main()
