"""Rebuild a compact index without editing any original measurement files."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent
entries = []
for path in sorted((root / 'results').glob('*/result.json')):
    record = json.loads(path.read_text(encoding='utf-8'))
    files = []
    for member in sorted(path.parent.iterdir()):
        if member.is_file():
            files.append({'path': member.relative_to(root).as_posix(),
                          'bytes': member.stat().st_size,
                          'sha256': hashlib.sha256(member.read_bytes()).hexdigest()})
    entries.append({'directory': path.parent.name, 'mode': record.get('mode', 'scope'),
                    'status': record['status'], 'error': record.get('error'), 'files': files})
(root / 'results-index.json').write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding='utf-8')
print('Indexed', len(entries), 'runs')
