"""Reversible scratch archiving and text-only, sanitized source export.

No hardware access. Credential files are excluded by path before opening.
"""
import argparse
from collections import Counter
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import uuid
import zipfile

SOURCE_ROOTS = {'src', 'scripts', 'sdk', 'host', 'pynq_overlay', 'rf_overlay',
                'ml_training', 'sdr_testbench', 'tools', 'skills', 'skill', 'report',
                'ci', 'docs', 'LICENSES', '.github'}
ROOT_FILES = {'README.md', 'AGENTS.md', 'CLAUDE.md', '.gitignore', '.gitattributes',
              'LICENSE', 'NOTICE', 'build_manifest.json'}
TEXT_SUFFIXES = {'.py', '.md', '.rst', '.txt', '.json', '.yaml', '.yml', '.toml',
                 '.tcl', '.xdc', '.v', '.sv', '.vh', '.vhd', '.vhdl', '.c', '.h',
                 '.cpp', '.hpp', '.sh', '.ps1', '.bat', '.cmd', '.ini', '.cfg',
                 '.csv', '.grc', '.service', '.qss', '.ui', '.qrc', '.mk',
                 '.coe', '.mem', '.hex', '.mif', '.sdc', '.example'}
SKIP_DIRS = {'.git', '.aws', '.codex', '.agents', '.claude', '.local', 'dist',
             '__pycache__', '.pytest_cache', '.venv', 'venv', 'node_modules',
             '.Xil', 'xsim.dir', '.obj', '.hostobj', 'vivado', 'out', 'out_merged',
             'hw', 'tmp_ws', 'datasets', 'captures', 'runs', 'results', 'logs',
             'reference', 'rtl_ref', 'generated', 'handoff', 'artifacts', 'evidence',
             '.board_branch_check', 'secrets', '.secrets', 'credentials', '.ssh',
             '.gnupg'}
# Third-party source kept byte-for-byte: transcoding would break its SHA256SUMS
# and the "verbatim copy" claim. No credentials live in these paths.
VERBATIM_PREFIXES = ('rf_overlay/sw/board_app/vendor/', 'rf_overlay/sw/ref/')
# Internal drafts that must stay in the private workspace (unfilled authorisation
# templates, personal notes); they are not part of the shareable snapshot.
PRIVATE_NAMES = {'OPEN-SOURCE-AUTHORIZATION-DRAFT.md'}
PRIVATE_NAME = re.compile(
    r'^(?:\.env(?:\..+)?|credentials(?:\..+)?|secrets?(?:\..+)?|'
    r'(?:access[_-]?|api[_-]?)?token(?:\..+)?|id_rsa(?:\..+)?|'
    r'id_ed25519(?:\..+)?|.*\.(?:pem|key|p12|pfx))$', re.I)
IP_PATTERN = re.compile(r'(?<![\d.])(?:192\.168\.\d{1,3}\.\d{1,3}|'
                        r'10\.\d{1,3}\.\d{1,3}\.\d{1,3}|'
                        r'172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?![\d.])')
CREDENTIAL = re.compile(
    r'''(?P<key>\b(?:[A-Z0-9_]*(?:PASSWORD|PASSWD|API_KEY|ACCESS_TOKEN|CLIENT_SECRET|SECRET_KEY)|password|passwd|pwd|token|secret)["']?\s*[:=]\s*)(?P<q>["'])(?P<value>[^"'\r\n]*)(?P=q)''', re.I)
SERIAL_FIELD = re.compile(r'''(?P<key>["'](?:serial|serial_number|hw_serial|device_serial)["']\s*:\s*)(?P<q>["'])(?P<value>[^"'\r\n]+)(?P=q)''', re.I)
VISA_SERIAL = re.compile(r'(USB\d+::(?:0x)?[\da-f]+::(?:0x)?[\da-f]+::)([^:\s"\']+)(::(?:\d+::)?INSTR)', re.I)
TYPED_DEVICE_SERIAL = re.compile(r'\b(?:CN|MY|SG|US)\d{8,12}\b')


def checked(root, path):
    root, path = Path(root).resolve(), Path(path).absolute()
    if path == root or root not in path.resolve().parents:
        raise ValueError('Target must stay strictly inside workspace')
    for part in [path] + list(path.parents):
        if part == root:
            break
        if part.is_symlink() or (part.exists() and
                getattr(part.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('Reparse points are not supported')
    return path


def private_path(path):
    return path.name != '.env.example' and bool(PRIVATE_NAME.match(path.name))


def scratch_name(name):
    return name.startswith('.pytest-work-') or name in {'.pytest_cache', '.Xil', 'dfx_runtime.txt', 'xvlog.pb'}


def metadata(path):
    """Inspect file metadata only; do not open temporary credential fixtures."""
    files, size = 0, 0
    for base, dirs, names in os.walk(path) if path.is_dir() else [(path.parent, [], [path.name])]:
        for name in dirs + names:
            checked(path.parent, Path(base) / name)
        for name in names:
            size += (Path(base) / name).stat().st_size
            files += 1
    return {'files': files, 'bytes': size}


def scratch_plan(root):
    result = subprocess.run(['git', 'ls-files', '-z'], cwd=str(root),
                            capture_output=True, check=True)
    tracked = {p.decode('utf-8') for p in result.stdout.split(b'\0') if p}
    cutoff = dt.datetime.now().timestamp() - 86400
    entries = []
    for path in sorted(root.iterdir()):
        if not scratch_name(path.name) or path.stat().st_mtime > cutoff:
            continue
        checked(root, path)
        if any(p == path.name or p.startswith(path.name + '/') for p in tracked):
            continue
        entries.append({'name': path.name, **metadata(path)})
    return entries


def write_json(path, value):
    text = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    if os.sys.version_info >= (3, 10):
        path.write_text(text, encoding='utf-8', newline='')
    else:
        path.write_bytes(text.encode('utf-8'))


def archive(root):
    entries = scratch_plan(root)
    label = dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    destination = checked(root, root / '.local' / 'archive' / label)
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {'schema': 1, 'kind': 'reversible-scratch-archive', 'entries': entries}
    manifest_path = destination / 'manifest.json'
    write_json(manifest_path, manifest)
    for item in entries:
        source, target = checked(root, root / item['name']), checked(root, destination / item['name'])
        if metadata(source) != {k: item[k] for k in ('files', 'bytes')}:
            raise ValueError('Scratch changed since planning')
        source.rename(target)
        item['moved'] = True
        write_json(manifest_path, manifest)
    return {'manifest': str(manifest_path.relative_to(root)), 'entries': len(entries),
            'files': sum(x['files'] for x in entries), 'bytes': sum(x['bytes'] for x in entries)}


def restore(root, manifest_path):
    manifest_path = checked(root, root / manifest_path)
    if root / '.local' / 'archive' not in manifest_path.parents:
        raise ValueError('Manifest must be inside .local/archive')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('kind') != 'reversible-scratch-archive':
        raise ValueError('Unexpected manifest kind')
    pairs = []
    for item in manifest['entries']:
        if Path(item['name']).name != item['name'] or not scratch_name(item['name']):
            raise ValueError('Unexpected archive member')
        source = checked(root, manifest_path.parent / item['name'])
        target = checked(root, root / item['name'])
        if source.exists():
            if target.exists():
                raise ValueError('Refusing to overwrite existing scratch')
            pairs.append((source, target))
    for source, target in pairs:
        source.rename(target)
    return {'restored_entries': len(pairs)}


def source_files(root):
    for base, dirs, names in os.walk(root):
        rel_base = Path(base).relative_to(root)
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and
                         not d.startswith(('.pytest-work-', 'ws', 'sim_')) and
                         not (Path(base) / d).is_symlink() and
                         not getattr((Path(base) / d).lstat(), 'st_file_attributes', 0) & 0x400 and
                         (rel_base.parts or d in SOURCE_ROOTS))
        for name in sorted(names):
            path = Path(base) / name
            rel = path.relative_to(root)
            if not rel_base.parts and name not in ROOT_FILES:
                continue
            if private_path(path) or path.is_symlink() or name in PRIVATE_NAMES:
                continue
            if name in {'dfx_runtime.txt', 'xvlog.pb'}:
                continue
            if name.endswith(('-output.txt', '.log', '.jou')):
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES and name not in {
                    'Makefile', 'CMakeLists.txt', '.gitignore', '.gitattributes',
                    'LICENSE', 'NOTICE', 'SHA256SUMS'}:
                continue
            checked(root, path)
            yield rel


def decode_text(raw, document=False):
    if b'\0' in raw:
        # A sparse stray NUL in a prose file is not a binary driver. Normalize
        # only the share copy; never silently change code or binary inputs.
        if document and raw.count(b'\0') < max(2, len(raw) // 100):
            raw = raw.replace(b'\0', b'')
        else:
            raise ValueError('Binary member')
    for encoding in ('utf-8-sig', 'gb18030', 'cp1252'):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    raise UnicodeError('Unsupported text encoding')


def read_text(path):
    return decode_text(path.read_bytes(), path.suffix.lower() in {'.md', '.rst', '.txt'})[0]


def is_device_identifier(value):
    return bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{7,63}', value)
                and any(c.isdigit() for c in value)
                and not value.upper().startswith(('TEST', 'MOCK', 'EXAMPLE', 'SERIAL')))


def device_ids(root, members):
    identifiers = set()
    for rel in members:
        # Only real device configuration, never test fixtures or credential paths.
        try:
            content = read_text(root / rel)
        except (UnicodeError, ValueError):
            continue
        if rel.suffix == '.json' and any(
                p in rel.parts for p in ('sdr_testbench', 'skills')) and any(
                p in rel.parts for p in ('config', 'gui_demo')):
            for match in SERIAL_FIELD.finditer(content):
                value = match['value']
                if is_device_identifier(value):
                    identifiers.add(value)
        identifiers.update(m[2] for m in VISA_SERIAL.finditer(content)
                           if is_device_identifier(m[2]))
        identifiers.update(TYPED_DEVICE_SERIAL.findall(content))
    return identifiers


def private_terms(root):
    """Load project-specific redaction terms from .local/redaction-terms.txt.

    The file stays in the private workspace and is never exported, so the exact
    personal identifiers do not appear in this public tool or in the snapshot.
    Format: ``term => replacement`` per line; '#' starts a comment.
    """
    path = Path(root) / '.local' / 'redaction-terms.txt'
    if not path.is_file():
        return []
    terms = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=>' not in line:
            continue
        term, replacement = (part.strip() for part in line.split('=>', 1))
        if term:
            terms.append((term, replacement or '<REDACTED>'))
    return terms


def redact(content, identifiers=(), username='', terms=()):
    counts = Counter()

    def substitute(label, pattern, replacement):
        nonlocal content
        content, count = re.subn(pattern, replacement, content)
        counts[label] += count

    # Whole personal Windows profile path first, including the user component.
    substitute('personal_windows_path',
               r'(?i)(?<![A-Za-z0-9])[A-Z]:[\\/]+Users[\\/]+[^\r\n"\'<>|]*',
               '<LOCAL_PATH>')
    # Other developer-machine absolute paths (tool roots, workspace roots, IDE
    # and toolchain installs). Only known roots are matched so that escape
    # sequences such as "status:\r\n" in source code are not touched.
    substitute('local_windows_path',
               r'(?i)(?<![A-Za-z0-9])[A-Z]:[\\/]+'
               r'(?:Program\s+Files(?:\s+\(x86\))?|ProgramData|Windows|Vivado_prj|Vivado|'
               r'vivado2025\.2|radioconda|mingw64|zynq7020src|ZYNQ_7020_AD9361|Xilinx|'
               r'Modelsim|intelFPGA|Git|Python[0-9.]*|Anaconda[0-9]*|tools)'
               r'(?:[\\/][A-Za-z0-9_\-.\\/(){}&%$#@!~+,=]*)?',
               '<LOCAL_PATH>')
    substitute('msys_windows_path',
               r'(?i)(?<![A-Za-z0-9_.-])/[a-z]/'
               r'(?:Vivado_prj|Vivado|vivado2025\.2|tmp|Users|Program\s+Files(?:\s+\(x86\))?|'
               r'ProgramData|Windows|radioconda|mingw64|zynq7020src|ZYNQ_7020_AD9361|Xilinx|'
               r'Modelsim|intelFPGA|Git|Python[0-9.]*|Anaconda[0-9]*|tools)'
               r'(?:/[A-Za-z0-9_\-.]+)*',
               '<LOCAL_PATH>')
    substitute('personal_path', r'(?i)([A-Z]:[\\/]+Users[\\/]+)[^\\/\r\n"\'<>]+',
               lambda m: m[1] + '<USER>')
    if username and username.lower() not in {'user', 'admin', 'administrator'}:
        counts['personal_name'] += content.count(username)
        content = content.replace(username, '<USER>')
    # user@host pairs (remote dev/session accounts) without embedding the
    # concrete account name or host address in this tool.
    substitute('remote_credentials',
               r'(?i)(?<![A-Za-z0-9_.-])([A-Za-z][A-Za-z0-9_.-]{1,31})@'
               r'((?:\d{1,3}\.){3}\d{1,3})(?![\d.])',
               '<REMOTE_USER>@<REMOTE_HOST>')
    for term, replacement in terms:
        counts['private_term'] += content.count(term)
        content = content.replace(term, replacement)
    substitute('private_address', IP_PATTERN, '192.0.2.10')
    substitute('visa_resource', r'(?i)USB\d+::[^\s"\'<>|]*::INSTR', '<VISA_RESOURCE>')
    for identifier in sorted(identifiers, key=len, reverse=True):
        counts['device_identifier'] += content.count(identifier)
        content = content.replace(identifier, '<DEVICE_SERIAL>')
    substitute('visa_serial', VISA_SERIAL, lambda m:
               m[1] + '<DEVICE_SERIAL>' + m[3] if is_device_identifier(m[2]) else m[0])
    substitute('credential_literal', CREDENTIAL,
               lambda m: m['key'] + m['q'] + ('<SET_LOCALLY>' if m['value'] else '') + m['q'])
    substitute('sudo_pipeline', r'(?i)echo\s+(?:["\'][^"\'\r\n]+["\']|[^\s|]+)\s*\|\s*sudo\s+-S',
               'echo $PYNQ_PASSWORD | sudo -S')
    substitute('url_credentials', r'(?i)((?:https?|ssh)://)[^/@\s]+:[^/@\s]+@',
               lambda m: m[1] + '<USER>:<PASSWORD>@')
    return content, +counts


def export(root):
    label = 'final-prj-source-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    destination = checked(root, root / 'dist' / label)
    destination.mkdir(parents=True, exist_ok=False)
    members = list(source_files(root))
    identifiers = device_ids(root, members)
    username = os.environ.get('USERNAME', '')
    terms = private_terms(root)
    records, skipped, totals, encodings = [], [], Counter(), Counter()
    for rel in members:
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        rel_posix = rel.as_posix()
        if rel_posix.startswith(VERBATIM_PREFIXES):
            raw = (root / rel).read_bytes()
            target.write_bytes(raw)
            encodings['verbatim-bytes'] += 1
            records.append({'path': rel_posix, 'sha256': hashlib.sha256(raw).hexdigest(),
                            'source_encoding': 'verbatim-bytes', 'verbatim': True,
                            'removed_document_nuls': raw.count(b'\0'), 'redactions': {}})
            continue
        try:
            raw = (root / rel).read_bytes()
            original, encoding = decode_text(raw, rel.suffix.lower() in {'.md', '.rst', '.txt'})
        except (UnicodeError, ValueError):
            skipped.append(rel.as_posix())
            continue
        text, counts = redact(original, identifiers, username, terms)
        # Snapshot repositories must be byte-stable across platforms: export LF
        # everywhere (the .gitattributes disables checkout conversion for the
        # rest), so SHARE-MANIFEST hashes stay valid on every clone.
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        target.write_text(text, encoding='utf-8', newline='') if os.sys.version_info >= (3, 10) else target.write_bytes(text.encode('utf-8'))
        totals.update(counts)
        encodings[encoding] += 1
        records.append({'path': rel_posix, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                        'source_encoding': encoding, 'removed_document_nuls': raw.count(b'\0'),
                        'redactions': dict(counts)})
    # Verify output separately; no original credentials or identifiers are printed.
    for record in records:
        if record.get('verbatim'):
            continue
        text = (destination / record['path']).read_text(encoding='utf-8')
        if IP_PATTERN.search(text) or TYPED_DEVICE_SERIAL.search(text) or any(identifier in text for identifier in identifiers):
            raise ValueError('Output privacy verification failed')
    manifest = {'schema': 1, 'kind': 'sanitized-text-source-not-installable-release',
                'excluded': ['git history', 'credentials', 'raw evidence', 'datasets',
                             'device binaries', 'images', 'archives', 'generated directories',
                             'GNU Radio Companion generated flowgraphs and GRC cache',
                             'reference-only third-party constraints (rf_overlay/rtl_ref)',
                             'simulation scratch and dfx runtime files',
                             'internal authorization drafts and personal notes',
                             'historical handoff and reference documents'],
                'undecodable_or_binary_text_paths': skipped, 'source_encodings': dict(encodings),
                'text_line_endings': 'lf', 'verbatim_roots': list(VERBATIM_PREFIXES),
                'redactions': dict(totals), 'files': records}
    write_json(destination / 'SHARE-MANIFEST.json', manifest)
    share_readme = (
        '# 脱敏源码副本\n\n这是可读源码与技能包，不是可安装发布包或原始验收证据。\n'
        '真实凭据、私网地址、设备序列号、开发机绝对路径、远端账户与授权别名已替换；个人姓名、'
        '手机号与个人邮箱不随本副本导出。使用前配置自己的环境。\n'
        '文本统一为 UTF-8 + LF；`rf_overlay/sw/board_app/vendor/` 与 `rf_overlay/sw/ref/` 按原始字节透传（清单标记 verbatim-bytes）。\n'
        '第三方版权与许可证见根 `LICENSE`、`NOTICE` 与 `LICENSES/`。\n'
        '原始IQ、数据集、图片、bit/HWH、发布归档、参考资料、Git历史及临时生成物未打包。\n'
        '原始实验收据仍保存在开发机；本文本副本不得用于验证旧发布摘要。\n'
        '需要完整仿真/历史回放时，须另行提供相应数据与构建依赖。\n')
    (destination / 'SHARE-README.md').write_bytes(share_readme.encode('utf-8'))
    archive_path = destination.with_suffix('.zip')
    with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(destination.rglob('*')):
            if path.is_file():
                bundle.write(path, path.relative_to(destination).as_posix())
    with zipfile.ZipFile(archive_path) as bundle:
        if bundle.testzip() is not None:
            raise ValueError('ZIP CRC check failed')
        for record in records:
            if hashlib.sha256(bundle.read(record['path'])).hexdigest() != record['sha256']:
                raise ValueError('ZIP content mismatch')
    return {'directory': str(destination.relative_to(root)), 'zip': str(archive_path.relative_to(root)),
            'files': len(records), 'zip_bytes': archive_path.stat().st_size,
            'redactions': dict(totals), 'skipped_text_files': len(skipped)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'archive', 'restore', 'export'))
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--manifest')
    args = parser.parse_args()
    root = args.root.resolve()
    if args.action == 'plan':
        result = scratch_plan(root)
    elif args.action == 'archive':
        result = archive(root)
    elif args.action == 'restore':
        if not args.manifest:
            parser.error('--manifest is required for restore')
        result = restore(root, args.manifest)
    else:
        result = export(root)
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
