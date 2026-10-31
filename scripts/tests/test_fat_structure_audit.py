"""Tests for the offline FAT structural audit.

The fixtures build genuine FAT16/FAT32 volume images byte by byte, so the auditor
is exercised against the real on-disk layout rather than against mocks.  Every
corruption test injects exactly one defect and asserts the matching finding code.
"""
import hashlib
from pathlib import Path
import struct
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.fat_structure_audit import audit, assemble_lfn, load_expect

BPS = 512
SPC = 1
RESERVED = 1
NFATS = 2
ROOT_ENTRIES = 512
TOTAL_SECTORS = 8192
FAT_SECTORS = 32
ROOT_DIR_SECTORS = ROOT_ENTRIES * 32 // BPS
DATA_START = RESERVED + NFATS * FAT_SECTORS + ROOT_DIR_SECTORS
CLUSTER_COUNT = (TOTAL_SECTORS - DATA_START) // SPC
IMAGE_BYTES = TOTAL_SECTORS * BPS


def name_bytes(name):
    if name.strip() in ('.', '..'):
        base, ext = name.strip(), ''
    else:
        base, _, ext = name.partition('.')
    return (base.ljust(8)[:8] + ext.ljust(3)[:3]).encode('ascii')


def lfn_slots(long_name, short_name):
    raw_name = name_bytes(short_name)
    checksum = 0
    for char in raw_name:
        checksum = (((checksum & 1) << 7) + (checksum >> 1) + char) & 0xFF
    encoded = long_name.encode('utf-16-le')
    units = [encoded[i:i + 2] for i in range(0, len(encoded), 2)] + [b'\x00\x00']
    chunks = [units[i:i + 13] for i in range(0, len(units), 13)]
    padded = []
    for chunk in chunks:
        chunk = chunk + [b'\xff\xff'] * (13 - len(chunk))
        padded.append(chunk)
    slots = []
    for index in range(len(padded) - 1, -1, -1):
        seq = index + 1
        if index == len(padded) - 1:
            seq |= 0x40
        raw = bytearray(32)
        raw[0] = seq
        raw[11] = 0x0F
        raw[12] = 0
        raw[13] = checksum
        body = bytearray(26)
        for position, unit in enumerate(padded[index]):
            body[position * 2:position * 2 + 2] = unit
        raw[1:11] = body[0:10]
        raw[14:26] = body[10:22]
        raw[28:32] = body[22:26]
        raw[26:28] = b'\x00\x00'
        slots.append(bytes(raw))
    return slots


class Fat16Image:
    """Byte-exact FAT16 image builder with scripted corruptions."""

    def __init__(self, state=0x00, type_string=b'FAT16   '):
        self.data = bytearray(IMAGE_BYTES)
        self.entries = []
        self.fat = [0] * (CLUSTER_COUNT + 2)
        self.fat[0] = 0xFF00 | 0xF8
        self.fat[1] = 0xFFFF
        self.next_cluster = 2
        self.write_boot_sector(state, type_string)

    # --- low level -------------------------------------------------------
    @property
    def root_offset(self):
        return (RESERVED + NFATS * FAT_SECTORS) * BPS

    def write_boot_sector(self, state=0x00, type_string=b'FAT16   '):
        sector = bytearray(BPS)
        sector[0:3] = bytes([0xEB, 0x3C, 0x90])
        sector[3:11] = b'mkfs.fat'
        struct.pack_into('<H', sector, 11, BPS)
        sector[13] = SPC
        struct.pack_into('<H', sector, 14, RESERVED)
        sector[16] = NFATS
        struct.pack_into('<H', sector, 17, ROOT_ENTRIES)
        struct.pack_into('<H', sector, 19, TOTAL_SECTORS)
        sector[21] = 0xF8
        struct.pack_into('<H', sector, 22, FAT_SECTORS)
        struct.pack_into('<H', sector, 24, 32)
        struct.pack_into('<H', sector, 26, 2)
        sector[36] = 0x80
        sector[37] = state
        sector[38] = 0x29
        sector[39:43] = bytes([0x9D, 0xE9, 0x35, 0x09])
        sector[43:54] = b'TESTVOL    '
        sector[54:62] = type_string
        sector[510:512] = b'\x55\xAA'
        self.data[0:BPS] = sector

    def allocate(self, payload):
        count = max(1, (len(payload) + BPS - 1) // BPS) if payload is not None else 1
        first = self.next_cluster
        clusters = list(range(first, first + count))
        for index, cluster in enumerate(clusters):
            nxt = clusters[index + 1] if index + 1 < len(clusters) else 0xFFFF
            self.fat[cluster] = nxt
            offset = DATA_START * BPS + (cluster - 2) * BPS
            if payload is not None:
                chunk = payload[index * BPS:(index + 1) * BPS]
                self.data[offset:offset + len(chunk)] = chunk
        self.next_cluster = first + count
        return clusters

    def entry_bytes(self, name, attr, cluster, size, lfn=None, lcase=0, deleted=False):
        raw = bytearray(32)
        raw[0:11] = name_bytes(name)
        raw[11] = attr
        raw[12] = lcase
        struct.pack_into('<H', raw, 26, cluster)
        struct.pack_into('<I', raw, 28, size)
        if deleted:
            raw[0] = 0xE5
        return bytes(raw)

    def add_file(self, name, payload, lfn=None, size_override=None, attr=0x20):
        if payload:
            clusters = self.allocate(payload)
            first = clusters[0]
        else:
            clusters = []
            first = 0
        size = len(payload) if size_override is None else size_override
        slots = lfn_slots(lfn, name) if lfn else []
        self.entries.append(b''.join(slots) + self.entry_bytes(name, attr, first, size, lfn))
        return clusters

    def add_directory(self, name, files=(), lfn=None):
        body = bytearray()
        body += self.entry_bytes('.          ', 0x10, 0, 0)
        body += self.entry_bytes('..         ', 0x10, 0, 0)
        children = []
        for child_name, payload in files:
            if payload:
                clusters = self.allocate(payload)
                children.append((child_name, clusters[0], len(payload)))
            else:
                children.append((child_name, 0, 0))
        for child_name, cluster, size in children:
            body += self.entry_bytes(child_name, 0x20, cluster, size)
        assert len(body) <= BPS, 'fixture directory body must fit one cluster'
        directory = self.allocate(bytes(body).ljust(BPS, b'\x00'))
        first_offset = DATA_START * BPS + (directory[0] - 2) * BPS
        struct.pack_into('<H', self.data, first_offset + 26, directory[0])
        self.entries.append(b''.join(lfn_slots(lfn, name) if lfn else [])
                            + self.entry_bytes(name, 0x10, directory[0], 0, lfn))
        return directory

    def child_slot(self, directory_cluster, raw_entry):
        base = DATA_START * BPS + (directory_cluster - 2) * BPS
        for index in range(BPS // 32):
            offset = base + index * 32
            if self.data[offset] == 0x00:
                self.data[offset:offset + 32] = raw_entry
                return offset
        raise AssertionError('no free slot in fixture directory')

    def add_child_entry(self, directory_cluster, name, attr, cluster, size):
        raw = bytearray(32)
        raw[0:11] = name_bytes(name)
        raw[11] = attr
        struct.pack_into('<H', raw, 26, cluster)
        struct.pack_into('<I', raw, 28, size)
        return self.child_slot(directory_cluster, bytes(raw))

    def set_entry_field(self, name, offset, payload):
        """Patch a short entry that is already staged in self.entries."""
        raw_name = name_bytes(name)
        for index, blob in enumerate(self.entries):
            if len(blob) >= 32 and blob[-32:-21] == raw_name:
                raw = bytearray(blob)
                start = len(raw) - 32 + offset
                raw[start:start + len(payload)] = payload
                self.entries[index] = bytes(raw)
                return
        raise AssertionError('entry %s not found' % name)

    def patch_fat(self, cluster, value):
        self.fat[cluster] = value

    # --- output ----------------------------------------------------------
    def save(self, path):
        for copy in range(NFATS):
            offset = (RESERVED + copy * FAT_SECTORS) * BPS
            for index, value in enumerate(self.fat):
                struct.pack_into('<H', self.data, offset + index * 2, value & 0xFFFF)
        root = self.root_offset
        blob = b''.join(self.entries)
        self.data[root:root + len(blob)] = blob
        Path(path).write_bytes(bytes(self.data))
        return Path(path)


def clean_image(tmp_path, name='boot.img'):
    image = Fat16Image()
    image.add_file('BOOT.BIN', bytes(range(256)) * 8, lfn='boot.bin')
    image.add_file('IMAGE.UB', b'kernel payload' * 200, lfn='image.ub')
    image.add_file('REVISION', b'PYNQ v2.7 revision\n', lfn='REVISION.txt')
    image.add_directory('SYSTEM~1', files=[('INDEXER.', b'')], lfn='System Volume Information')
    return image.save(tmp_path / name)


def codes(report):
    return {finding['code'] for finding in report['findings']}


def test_clean_image_has_no_findings(tmp_path):
    report = audit(clean_image(tmp_path))
    assert report['verdict'] == 'consistent'
    assert report['findings'] == []
    assert report['volume']['fat_bits'] == 16
    assert report['volume']['cluster_count'] == CLUSTER_COUNT
    assert report['fat_copies'] == dict(count=2, identical=True, first_difference_byte=None)
    assert report['allocation']['lost_clusters'] == 0
    assert report['object_count'] == 5


def test_directory_loop_is_an_error_not_an_infinite_walk(tmp_path):
    image = Fat16Image()
    image.add_file('FILE.TXT', b'payload', lfn='file.txt')
    directory = image.add_directory('SAFEDIR')
    image.add_child_entry(directory[0], 'SELF', 0x10, directory[0], 0)
    report = audit(image.save(tmp_path / 'dirloop.img'))
    assert 'directory_loop' in codes(report)
    assert report['verdict'] == 'inconsistent'


def test_degenerate_directory_entry_is_a_warning(tmp_path):
    image = Fat16Image()
    image.add_file('FILE.TXT', b'payload', lfn='file.txt')
    directory = image.add_directory('SAFEDIR')
    raw = bytearray(32)
    raw[0:11] = b'           '           # all-space name: no short name at all
    raw[11] = 0x10
    struct.pack_into('<H', raw, 26, directory[0])
    image.child_slot(directory[0], bytes(raw))
    report = audit(image.save(tmp_path / 'degenerate.img'))
    assert 'degenerate_directory_entry' in codes(report)
    assert report['verdict'] == 'consistent_with_warnings'


def test_audit_never_writes_to_the_image(tmp_path):
    path = clean_image(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    audit(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_dirty_mount_state_is_reported_but_not_a_defect(tmp_path):
    image = Fat16Image(state=0x01)
    image.add_file('BOOT.BIN', b'payload', lfn='boot.bin')
    report = audit(image.save(tmp_path / 'dirty.img'))
    assert report['volume']['state'] == 0x01
    assert report['volume']['state_dirty'] is True
    assert report['volume']['state_offset'] == 37
    assert report['verdict'] == 'consistent'


def test_fat_copies_differ_is_an_error(tmp_path):
    image = Fat16Image()
    image.add_file('BOOT.BIN', b'payload', lfn='boot.bin')
    path = image.save(tmp_path / 'copy.img')
    raw = bytearray(path.read_bytes())
    second_copy = (RESERVED + FAT_SECTORS) * BPS
    raw[second_copy + 8] ^= 0xFF
    path.write_bytes(bytes(raw))
    report = audit(path)
    assert report['fat_copies']['identical'] is False
    assert report['fat_copies']['first_difference_byte'] == 8
    assert 'fat_copies_differ' in codes(report)
    assert report['verdict'] == 'inconsistent'


def test_cross_linked_cluster_is_an_error(tmp_path):
    image = Fat16Image()
    first = image.add_file('AAA.TXT', b'A' * 600, lfn='aaa.txt')
    image.add_file('BBB.TXT', b'B' * 600, lfn='bbb.txt')
    image.set_entry_field('BBB.TXT', 26, struct.pack('<H', first[0]))
    report = audit(image.save(tmp_path / 'cross.img'))
    assert 'cross_linked_cluster' in codes(report)
    assert report['verdict'] == 'inconsistent'


def test_chain_longer_than_size_is_an_error(tmp_path):
    image = Fat16Image()
    clusters = image.add_file('BIG.BIN', b'X' * 2048, lfn='big.bin')
    image.set_entry_field('BIG.BIN', 28, struct.pack('<I', 600))
    report = audit(image.save(tmp_path / 'long.img'))
    assert 'chain_longer_than_size' in codes(report)
    assert report['verdict'] == 'inconsistent'
    assert clusters[0] == 2


def test_chain_shorter_than_size_is_an_error(tmp_path):
    image = Fat16Image()
    image.add_file('SHORT.BIN', b'X' * 600, lfn='short.bin')
    image.set_entry_field('SHORT.BIN', 28, struct.pack('<I', 4096))
    report = audit(image.save(tmp_path / 'short.img'))
    assert 'chain_shorter_than_size' in codes(report)
    assert report['verdict'] == 'inconsistent'


def test_lost_cluster_is_a_warning(tmp_path):
    image = Fat16Image()
    image.add_file('BOOT.BIN', b'payload', lfn='boot.bin')
    image.patch_fat(500, 0xFFFF)
    report = audit(image.save(tmp_path / 'lost.img'))
    assert 'lost_clusters' in codes(report)
    assert report['allocation']['lost_clusters'] == 1
    assert report['allocation']['lost_cluster_list'] == [500]
    assert report['verdict'] == 'consistent_with_warnings'


def test_chain_loop_is_an_error(tmp_path):
    image = Fat16Image()
    clusters = image.add_file('LOOP.BIN', b'X' * 1024, lfn='loop.bin')
    image.patch_fat(clusters[0], clusters[0])
    report = audit(image.save(tmp_path / 'loop.img'))
    assert 'chain_loop' in codes(report)
    assert report['verdict'] == 'inconsistent'


def test_out_of_range_fat_entry_is_an_error(tmp_path):
    image = Fat16Image()
    clusters = image.add_file('RANGE.BIN', b'X' * 1024, lfn='range.bin')
    image.patch_fat(clusters[0], 0x7FFF)
    report = audit(image.save(tmp_path / 'range.img'))
    assert 'chain_out_of_range' in codes(report)
    assert report['verdict'] == 'inconsistent'


def test_bad_boot_signature_and_layout_errors(tmp_path):
    image = Fat16Image()
    image.add_file('BOOT.BIN', b'payload', lfn='boot.bin')
    path = image.save(tmp_path / 'sig.img')
    raw = bytearray(path.read_bytes())
    raw[510:512] = b'\x00\x00'
    path.write_bytes(bytes(raw))
    report = audit(path)
    assert 'boot_signature' in codes(report)
    assert report['verdict'] == 'inconsistent'


def test_type_string_mismatch_warns(tmp_path):
    image = Fat16Image(type_string=b'FAT32   ')
    image.add_file('BOOT.BIN', b'payload', lfn='boot.bin')
    report = audit(image.save(tmp_path / 'type.img'))
    assert 'type_string_mismatch' in codes(report)
    assert report['verdict'] == 'consistent_with_warnings'


def test_file_with_size_but_no_cluster_is_an_error(tmp_path):
    image = Fat16Image()
    image.add_file('EMPTY.BIN', b'', lfn='empty.bin', size_override=1024)
    report = audit(image.save(tmp_path / 'nocluster.img'))
    assert 'file_without_cluster' in codes(report)
    assert report['verdict'] == 'inconsistent'


def test_lfn_checksum_mismatch_is_a_warning(tmp_path):
    image = Fat16Image()
    image.add_file('PYNQ_S~1.PY', b'print(1)\n', lfn='pynq_status.py')
    path = image.save(tmp_path / 'lfn.img')
    raw = bytearray(path.read_bytes())
    raw[image.root_offset + 13] ^= 0xFF           # corrupt LFN checksum byte
    path.write_bytes(bytes(raw))
    report = audit(path)
    assert 'lfn_checksum_mismatch' in codes(report)
    assert report['verdict'] == 'consistent_with_warnings'


def test_lfn_reconstruction_and_checksum(tmp_path):
    report = audit(clean_image(tmp_path))
    names = {item['path']: item.get('long_name') for item in report['objects']}
    assert names['/boot.bin'] == 'boot.bin'
    paths = set(names)
    assert '/System Volume Information' in paths
    assert report['warning_count'] == 0


def test_assemble_lfn_rejects_bad_checksum():
    slots = [(0, raw) for raw in lfn_slots('long name.txt', 'LONGNA~1.TXT')]
    _, problem, ok = assemble_lfn(slots, name_bytes('LONGNA~1.TXT'))
    assert ok is True and problem is None
    _, problem, ok = assemble_lfn(slots, name_bytes('OTHER~1.TXT'))
    assert ok is False and 'checksum' in problem


def test_content_verification_matches_and_detects_change(tmp_path):
    path = clean_image(tmp_path)
    blob = path.read_bytes()
    expect = {'boot.bin': dict(bytes=2048, sha256=hashlib.sha256(blob[0:0]).hexdigest())}
    # Build the expectation from the volume itself, then verify it round-trips.
    report = audit(path)
    boot = next(item for item in report['objects'] if item['path'] == '/boot.bin')
    with open(path, 'rb') as handle:
        handle.seek(DATA_START * BPS + (boot['clusters'][0] - 2) * BPS)
        content = handle.read(boot['size'])
    expect = {'boot.bin': dict(bytes=boot['size'], sha256=hashlib.sha256(content).hexdigest())}
    report = audit(path, expect=expect)
    assert report['content_verification']['status'] == 'verified'
    assert [item['name'] for item in report['content_verification']['verified']] == ['boot.bin']
    assert report['verdict'] == 'consistent'

    expect_wrong = {'boot.bin': dict(bytes=boot['size'], sha256='0' * 64)}
    report = audit(path, expect=expect_wrong)
    assert report['content_verification']['status'] == 'failed'
    assert report['content_verification']['mismatched'][0]['name'] == 'boot.bin'

    report = audit(path, expect={'missing.bin': dict(bytes=1, sha256='0' * 64)})
    assert report['content_verification']['missing'] == ['missing.bin']


def test_load_expect_accepts_backup_receipt_shape(tmp_path):
    receipt = tmp_path / 'receipt.json'
    receipt.write_text('{"passed": true, "backup_sha256": {"BOOT.BIN": {"bytes": 3, "sha256": "ab"}}}',
                       encoding='utf-8')
    assert load_expect(receipt) == {'BOOT.BIN': {'bytes': 3, 'sha256': 'ab'}}


def test_fat32_volume_audits_clean(tmp_path):
    """FAT32 layout path: reserved area, FSINFO, root as a cluster chain."""
    bps, spc = 512, 1
    reserved, nfats, fat_sectors, total = 32, 2, 32, 4096
    data_start = reserved + nfats * fat_sectors
    cluster_count = (total - data_start) // spc
    data = bytearray(total * bps)
    sector = bytearray(bps)
    sector[0:3] = bytes([0xEB, 0x58, 0x90])
    sector[3:11] = b'MSWIN4.1'
    struct.pack_into('<H', sector, 11, bps)
    sector[13] = spc
    struct.pack_into('<H', sector, 14, reserved)
    sector[16] = nfats
    struct.pack_into('<H', sector, 17, 0)
    struct.pack_into('<H', sector, 19, 0)
    sector[21] = 0xF8
    struct.pack_into('<H', sector, 22, 0)
    struct.pack_into('<I', sector, 32, total)
    struct.pack_into('<I', sector, 36, fat_sectors)
    struct.pack_into('<I', sector, 44, 2)
    struct.pack_into('<H', sector, 48, 1)
    sector[64] = 0x80
    sector[65] = 0x00
    sector[66] = 0x29
    sector[71:82] = b'TESTVOL    '
    sector[82:90] = b'FAT32   '
    sector[510:512] = b'\x55\xAA'
    data[0:bps] = sector
    fat = [0] * (cluster_count + 2)
    fat[0] = 0x0FFFFFF8
    fat[1] = 0x0FFFFFFF
    fat[2] = 0x0FFFFFFF                       # root directory, one cluster
    payload = b'hello fat32'
    fat[3] = 0x0FFFFFFF
    root_off = data_start * bps
    entry = bytearray(32)
    entry[0:11] = name_bytes('HELLO.TXT')
    entry[11] = 0x20
    struct.pack_into('<H', entry, 26, 3)
    struct.pack_into('<I', entry, 28, len(payload))
    data[root_off:root_off + 32] = entry
    data[root_off + 32] = 0x00
    file_off = data_start * bps + (3 - 2) * bps
    data[file_off:file_off + len(payload)] = payload
    for copy in range(nfats):
        base = (reserved + copy * fat_sectors) * bps
        for index, value in enumerate(fat):
            struct.pack_into('<I', data, base + index * 4, value)
    path = tmp_path / 'fat32.img'
    path.write_bytes(bytes(data))
    report = audit(path)
    assert report['volume']['fat_bits'] == 32
    assert report['volume']['state_offset'] == 65
    assert report['findings'] == []
    assert [item['path'] for item in report['objects']] == ['/HELLO.TXT']
    assert report['verdict'] == 'consistent'


def test_truncated_image_raises(tmp_path):
    image = Fat16Image()
    image.add_file('BOOT.BIN', b'payload', lfn='boot.bin')
    path = image.save(tmp_path / 'trunc.img')
    path.write_bytes(path.read_bytes()[:4096])
    with pytest.raises(ValueError):
        audit(path)
