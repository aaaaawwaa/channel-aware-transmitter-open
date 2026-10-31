import importlib.util
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('maintenance', Path(__file__).with_name('project_maintenance.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_traversal_and_root_refused(self):
        for target in (self.root, self.root / '..' / 'outside'):
            with self.assertRaises(ValueError):
                m.checked(self.root, target)

    def test_secret_paths_excluded_before_read(self):
        (self.root / 'src').mkdir()
        (self.root / 'src' / 'token.txt').write_bytes(b'not to be opened')
        (self.root / 'src' / '.env').write_bytes(b'not to be opened')
        (self.root / 'src' / 'model.json').write_text('{"weights":[0.125,-0.25]}')
        self.assertEqual(list(m.source_files(self.root)), [Path('src/model.json')])

    def test_redaction_preserves_code_and_model_numbers(self):
        import ast
        original = 'password="<SET_LOCALLY>"\ncmd="echo $PYNQ_PASSWORD | sudo -S thing"\n'
        original += 'host="192.0.2.10"\nserial="REAL_DEVICE_123"\nweights=[0.125,-0.25]\n'
        text, counts = m.redact(original, {'REAL_DEVICE_123'})
        ast.parse(text)
        self.assertNotIn('private-value', text)
        self.assertNotIn('REAL_DEVICE_123', text)
        self.assertNotIn('192.' + '168.2.9', text)
        self.assertIn('weights=[0.125,-0.25]', text)
        self.assertGreater(sum(counts.values()), 0)

    def test_archive_restore_preserves_bytes_refuses_overwrite(self):
        scratch = self.root / '.pytest-work-example'
        scratch.mkdir()
        (scratch / 'fixture.bin').write_bytes(b'unchanged\x00bytes')
        old = 1000000000
        os.utime(scratch, (old, old))
        with patch.object(m.subprocess, 'run') as run:
            run.return_value.stdout = b'src/main.py\0'
            result = m.archive(self.root)
        self.assertFalse(scratch.exists())
        scratch.mkdir()
        with self.assertRaises(ValueError):
            m.restore(self.root, result['manifest'])
        scratch.rmdir()
        m.restore(self.root, result['manifest'])
        self.assertEqual((scratch / 'fixture.bin').read_bytes(), b'unchanged\x00bytes')

    def test_tracked_scratch_never_archived(self):
        scratch = self.root / '.pytest-work-tracked'
        scratch.mkdir()
        os.utime(scratch, (1000000000, 1000000000))
        with patch.object(m.subprocess, 'run') as run:
            run.return_value.stdout = b'.pytest-work-tracked/receipt.json\0'
            self.assertEqual(m.scratch_plan(self.root), [])

    def test_export_does_not_mutate_source_or_include_binary_evidence(self):
        source = self.root / 'src'
        source.mkdir()
        original = '{"password":"<SET_LOCALLY>","weights":[0.125,-0.25]}'
        (source / 'model.json').write_text(original)
        (source / 'photo.png').write_bytes(b'\x00binary')
        evidence = self.root / 'evidence'
        evidence.mkdir()
        (evidence / 'receipt.json').write_text('{"immutable":true}')
        result = m.export(self.root)
        self.assertEqual((source / 'model.json').read_text(), original)
        with zipfile.ZipFile(self.root / result['zip']) as bundle:
            self.assertNotIn('evidence/receipt.json', bundle.namelist())
            self.assertNotIn('src/photo.png', bundle.namelist())
            data = json.loads(bundle.read('src/model.json'))
            self.assertEqual(data['weights'], [0.125, -0.25])
            self.assertEqual(data['password'], '<SET_LOCALLY>')

    def test_local_paths_and_remote_accounts_are_redacted(self):
        original = (
            'root=<LOCAL_PATH>'
            'tool=<LOCAL_PATH>'
            'home=<LOCAL_PATH>'
            'bash=<LOCAL_PATH>\n'
            "remote='<REMOTE_USER>@<REMOTE_HOST>'\n"
            'visa="<VISA_RESOURCE>"\n'
        )
        text, counts = m.redact(original, {'<DEVICE_SERIAL>'})
        for leaked in ('ProgramData', 'Program Files', 'C:/Users', 'Vivado_prj',
                       'exampleuser', 'USB0::'):
            self.assertNotIn(leaked, text)
        self.assertIn('<LOCAL_PATH>', text)
        self.assertIn('<REMOTE_USER>', text)
        self.assertIn('<VISA_RESOURCE>', text)

    def test_private_terms_file_is_applied_and_not_exported(self):
        source = self.root / 'src'
        source.mkdir()
        (source / 'note.md').write_bytes(b'confirmation by Sensitive Alias\n')
        local = self.root / '.local'
        local.mkdir()
        (local / 'redaction-terms.txt').write_bytes(
            b'# private terms\nSensitive Alias => <AUTHORIZER>\n')
        result = m.export(self.root)
        with zipfile.ZipFile(self.root / result['zip']) as bundle:
            names = bundle.namelist()
            self.assertNotIn('src/note.md', [n for n in names if 'redaction-terms' in n])
            text = bundle.read('src/note.md').decode('utf-8')
        self.assertIn('<AUTHORIZER>', text)
        self.assertNotIn('Sensitive Alias', text)
        self.assertFalse(any('.local' in name for name in names))

    def test_internal_authorization_draft_is_excluded(self):
        docs = self.root / 'docs'
        docs.mkdir()
        (docs / 'OPEN-SOURCE-AUTHORIZATION-DRAFT.md').write_text('draft')
        (docs / 'other.md').write_text('ok')
        self.assertEqual(list(m.source_files(self.root)), [Path('docs/other.md')])

    def test_skill_and_report_entry_dirs_are_exported(self):
        for directory in ('skill', 'report'):
            (self.root / directory).mkdir()
            (self.root / directory / 'README.md').write_bytes(b'# entry\n')
        result = m.export(self.root)
        with zipfile.ZipFile(self.root / result['zip']) as bundle:
            names = bundle.namelist()
        self.assertIn('skill/README.md', names)
        self.assertIn('report/README.md', names)

    def test_ci_workflow_and_build_manifest_are_exported(self):
        (self.root / '.github' / 'workflows').mkdir(parents=True)
        (self.root / '.github' / 'workflows' / 'ci.yml').write_bytes(b'name: ci\n')
        (self.root / 'build_manifest.json').write_bytes(b'{"manifest_version": 2}\n')
        result = m.export(self.root)
        with zipfile.ZipFile(self.root / result['zip']) as bundle:
            names = bundle.namelist()
        self.assertIn('.github/workflows/ci.yml', names)
        self.assertIn('build_manifest.json', names)

    def test_skill_testbench_config_serial_is_redacted(self):
        config = self.root / 'skills' / 'demo' / 'assets' / 'testbench' / 'config'
        config.mkdir(parents=True)
        serial = 'a1' * 16
        (config / 'pluto.json').write_bytes(
            ('{"serial": "%s", "frequency": 433900000}\n' % serial).encode('utf-8'))
        (self.root / 'skills' / 'demo' / 'README.md').write_bytes(
            ('device serial %s used for the demo\n' % serial).encode('utf-8'))
        result = m.export(self.root)
        with zipfile.ZipFile(self.root / result['zip']) as bundle:
            text = bundle.read('skills/demo/README.md')
            self.assertNotIn(serial.encode('utf-8'), text)
            self.assertIn(b'<DEVICE_SERIAL>', text)

    def test_curated_docs_evidence_is_exported_but_raw_evidence_is_not(self):
        (self.root / 'docs' / 'evidence').mkdir(parents=True)
        (self.root / 'docs' / 'evidence' / 'README.md').write_bytes(b'# public digest\n')
        (self.root / 'evidence').mkdir()
        (self.root / 'evidence' / 'raw-receipt.json').write_bytes(b'{"raw": true}\n')
        result = m.export(self.root)
        with zipfile.ZipFile(self.root / result['zip']) as bundle:
            names = bundle.namelist()
        self.assertIn('docs/evidence/README.md', names)
        self.assertNotIn('evidence/raw-receipt.json', names)

    def test_whitelisted_diagram_is_exported_verbatim_but_other_images_are_not(self):
        figures = self.root / 'docs' / 'figures'
        figures.mkdir(parents=True)
        whitelisted = figures / 'rf-system-architecture-poster-4x3.png'
        payload = b'\x89PNG\r\n\x1a\n' + b'public-diagram-bytes'
        whitelisted.write_bytes(payload)
        (figures / 'private-photo.png').write_bytes(b'\x89PNG\r\n\x1a\n' + b'private-photo')
        result = m.export(self.root)
        manifest = json.loads(
            (self.root / result['directory'] / 'SHARE-MANIFEST.json').read_text(encoding='utf-8'))
        with zipfile.ZipFile(self.root / result['zip']) as bundle:
            names = bundle.namelist()
            self.assertIn('docs/figures/rf-system-architecture-poster-4x3.png', names)
            self.assertNotIn('docs/figures/private-photo.png', names)
            self.assertEqual(payload, bundle.read('docs/figures/rf-system-architecture-poster-4x3.png'))
        record = next(r for r in manifest['files']
                      if r['path'] == 'docs/figures/rf-system-architecture-poster-4x3.png')
        self.assertEqual(record['source_encoding'], 'verbatim-binary')
        self.assertEqual(manifest['source_encodings'].get('verbatim-binary'), 1)

    def test_license_and_notice_are_exported(self):
        licenses = self.root / 'LICENSES'
        licenses.mkdir()
        (licenses / 'Third-Party-BSD-3-Clause.txt').write_bytes(
            b'BSD 3-Clause License\nCopyright (c) 2026 Example Project\n')
        (self.root / 'LICENSE').write_bytes(b'MIT License\nCopyright (c) 2026 aaaaawwaa\n')
        (self.root / 'NOTICE').write_bytes(b'Third-party components: see LICENSES/.\n')
        (self.root / 'unlisted-secret.txt').write_text('not exported')
        result = m.export(self.root)
        with zipfile.ZipFile(self.root / result['zip']) as bundle:
            names = bundle.namelist()
            self.assertIn('LICENSE', names)
            self.assertIn('NOTICE', names)
            self.assertIn('LICENSES/Third-Party-BSD-3-Clause.txt', names)
            self.assertNotIn('unlisted-secret.txt', names)
            self.assertEqual(bundle.read('LICENSE').decode('utf-8'),
                             'MIT License\nCopyright (c) 2026 aaaaawwaa\n')

    def test_vendor_and_reference_sources_are_exported_verbatim(self):
        vendor = self.root / 'rf_overlay' / 'sw' / 'board_app' / 'vendor'
        vendor.mkdir(parents=True)
        original = '// 旧编码注释\nint f(void) { return 0; }\n'.encode('gb18030')
        (vendor / 'driver.c').write_bytes(original)
        (vendor / 'SHA256SUMS').write_bytes(b'a' * 64 + b'  driver.c\n')
        result = m.export(self.root)
        with zipfile.ZipFile(self.root / result['zip']) as bundle:
            names = bundle.namelist()
            self.assertIn('rf_overlay/sw/board_app/vendor/driver.c', names)
            self.assertIn('rf_overlay/sw/board_app/vendor/SHA256SUMS', names)
            self.assertEqual(bundle.read('rf_overlay/sw/board_app/vendor/driver.c'), original)
        manifest = json.loads((self.root / result['directory'] / 'SHARE-MANIFEST.json').read_text(encoding='utf-8'))
        record = [r for r in manifest['files'] if r['path'].endswith('driver.c')][0]
        self.assertEqual(record['source_encoding'], 'verbatim-bytes')
        self.assertEqual(record['sha256'], hashlib.sha256(original).hexdigest())
        self.assertEqual(manifest['source_encodings']['verbatim-bytes'], 2)

    def test_generated_and_reference_dirs_are_excluded(self):
        for directory in ('sdr_testbench/generated', 'sdr_testbench/gui_demo/generated',
                          'rf_overlay/rtl_ref', 'pynq_overlay/tb/sim_fb'):
            (self.root / directory).mkdir(parents=True)
            (self.root / directory / 'x.py').write_text('x = 1\n')
        (self.root / 'pynq_overlay' / 'dfx_runtime.txt').write_text('x')
        result = m.export(self.root)
        with zipfile.ZipFile(self.root / result['zip']) as bundle:
            names = bundle.namelist()
        for directory in ('generated/', 'rtl_ref/', 'sim_fb/', 'dfx_runtime.txt'):
            self.assertFalse(any(directory in name for name in names),
                             'unexpected %s in bundle' % directory)

    def test_legacy_encoding_source_is_included(self):
        source = self.root / 'src'
        source.mkdir()
        content = '// 旧编码注释\nint main(void) { return 0; }\n'
        original = content.encode('gb18030')
        (source / 'driver.c').write_bytes(original)
        result = m.export(self.root)
        self.assertEqual((source / 'driver.c').read_bytes(), original)
        with zipfile.ZipFile(self.root / result['zip']) as bundle:
            self.assertEqual(bundle.read('src/driver.c').decode('utf-8'), content)

    def test_scope_interface_serial_is_redacted_without_touching_hashes(self):
        source = self.root / 'src'
        source.mkdir()
        serial = 'CN' + '12345678'
        content = f'<VISA_RESOURCE>'
        (source / 'scope.py').write_text(repr(content), encoding='utf-8')
        identifiers = m.device_ids(self.root, [Path('src/scope.py')])
        self.assertIn(serial, identifiers)
        digest, release = 'a' * 64, 'b' * 32
        text, _ = m.redact(content + '\n' + digest + '\n' + release, identifiers)
        self.assertNotIn(serial, text)
        self.assertIn('<VISA_RESOURCE>', text)
        self.assertIn(digest, text)
        self.assertIn(release, text)

    def test_sparse_document_nul_normalized_only_in_share(self):
        docs = self.root / 'docs'
        docs.mkdir()
        original = b'# Index\n\x00Preserve prose.\n'
        (docs / 'README.md').write_bytes(original)
        result = m.export(self.root)
        self.assertEqual((docs / 'README.md').read_bytes(), original)
        with zipfile.ZipFile(self.root / result['zip']) as bundle:
            self.assertEqual(bundle.read('docs/README.md'), original.replace(b'\0', b''))
        with self.assertRaises(ValueError):
            m.decode_text(b'int\0main(void);')

    def test_device_template_is_not_a_real_identifier(self):
        source = self.root / 'src'
        source.mkdir()
        template = 'f"<VISA_RESOURCE>"'
        (source / 'template.py').write_text(template, encoding='utf-8')
        identifiers = m.device_ids(self.root, [Path('src/template.py')])
        self.assertNotIn('{serial}', identifiers)
        text, _ = m.redact(template, identifiers)
        self.assertIn('<VISA_RESOURCE>', text)


if __name__ == '__main__':
    unittest.main()
