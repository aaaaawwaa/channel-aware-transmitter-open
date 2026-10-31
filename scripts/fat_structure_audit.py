"""Read-only structural audit of a FAT12/16/32 volume image.

Why this exists
---------------
The PYNQ board carries no FAT checking tool (fsck.fat / dosfsck / busybox are all
absent, see evidence .../storage-tools.json) and this host has none either.  The
boot partition's only useful maintenance path is therefore: capture the volume
read-only, then audit it offline where the work is repeatable and reviewable.

Scope and honesty
-----------------
This is **not** dosfsck/fsck.fat and must never be reported as one.  It performs a
read-only structural audit:

* BPB/layout sanity and the derived FAT width against the declared type string;
* both FAT copies must be byte-identical;
* every cluster chain: entry range, loop detection, length against the recorded
  size, first-cluster sanity;
* cross-linked clusters (two objects owning one cluster) and lost clusters
  (allocated but unreachable);
* directory entries: attribute sanity, LFN slot sequence/checksum, orphan slots;
* optional byte-for-byte verification of expected files (``--expect``), which is
  what proves a boot-file backup is a faithful copy of the card.

It never writes to the image or the device.  Repair is out of scope by design.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re
import sys

FAT_STATE_DIRTY = 0x01
ATTR_RO, ATTR_HIDDEN, ATTR_SYS, ATTR_VOLUME = 0x01, 0x02, 0x04, 0x08
ATTR_DIR, ATTR_ARCH, ATTR_LFN = 0x10, 0x20, 0x0F
RESERVED_ENTRIES = 2
WINDOWS_DRIVE = re.compile(r'^\\\\\.\\[A-Za-z]:$')


class Image:
    """Byte-addressable read-only view of an image file or raw volume."""

    def __init__(self, path):
        self.path = str(path)
        self.raw = None
        try:
            self.handle = open(self.path, 'rb')
            self.size = self._size()
        except OSError as exc:
            raise ValueError('cannot open %s read-only: %s' % (self.path, exc)) from exc

    def _size(self):
        if WINDOWS_DRIVE.match(self.path):
            # Raw volumes have no meaningful file size; callers must pass --bytes.
            return None
        self.handle.seek(0, 2)
        return self.handle.tell()

    def read(self, offset, size):
        if offset < 0 or size < 0:
            raise ValueError('negative read range')
        self.handle.seek(offset)
        data = self.handle.read(size)
        if len(data) != size:
            raise ValueError('short read at %d: wanted %d got %d' % (offset, size, len(data)))
        return data

    def close(self):
        self.handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def u16(data, offset):
    return int.from_bytes(data[offset:offset + 2], 'little')


def u32(data, offset):
    return int.from_bytes(data[offset:offset + 4], 'little')


def parse_bpb(boot_sector, image_bytes=None):
    """Parse and sanity-check the BIOS parameter block of a boot sector."""
    if len(boot_sector) < 512:
        raise ValueError('boot sector shorter than 512 bytes')
    bpb = dict(
        oem=boot_sector[3:11].decode('ascii', 'replace'),
        bytes_per_sector=u16(boot_sector, 11),
        sectors_per_cluster=boot_sector[13],
        reserved_sectors=u16(boot_sector, 14),
        num_fats=boot_sector[16],
        root_entries=u16(boot_sector, 17),
        total_sectors_16=u16(boot_sector, 19),
        media=boot_sector[21],
        fat_size_16=u16(boot_sector, 22),
        sectors_per_track=u16(boot_sector, 24),
        num_heads=u16(boot_sector, 26),
        hidden_sectors=u32(boot_sector, 28),
        total_sectors_32=u32(boot_sector, 32),
        fat_size_32=u32(boot_sector, 36),
        boot_signature=boot_sector[510:512].hex(),
        jump=boot_sector[0:3].hex(),
    )
    fat32 = bpb['fat_size_16'] == 0 and bpb['fat_size_32'] != 0
    bpb['fat_size_sectors'] = bpb['fat_size_32'] if fat32 else bpb['fat_size_16']
    bpb['total_sectors'] = bpb['total_sectors_16'] or bpb['total_sectors_32']
    bpb['root_dir_sectors'] = ((bpb['root_entries'] * 32) + (bpb['bytes_per_sector'] - 1)) // max(bpb['bytes_per_sector'], 1)
    bpb['data_start_sector'] = (bpb['reserved_sectors'] + bpb['num_fats'] * bpb['fat_size_sectors']
                                + (0 if fat32 else bpb['root_dir_sectors']))
    if fat32:
        bpb['root_cluster'] = u32(boot_sector, 44)
        bpb['fsinfo_sector'] = u16(boot_sector, 48)
        bpb['label'] = boot_sector[71:82].decode('ascii', 'replace')
        bpb['type_string'] = boot_sector[82:90].decode('ascii', 'replace')
        bpb['state_offset'] = 65
    else:
        bpb['root_cluster'] = 0
        bpb['fsinfo_sector'] = 0
        bpb['label'] = boot_sector[43:54].decode('ascii', 'replace')
        bpb['type_string'] = boot_sector[54:62].decode('ascii', 'replace')
        bpb['state_offset'] = 37
    bpb['state'] = boot_sector[bpb['state_offset']]
    bpb['state_dirty'] = bool(bpb['state'] & FAT_STATE_DIRTY)
    spc = bpb['sectors_per_cluster'] or 1
    bpb['cluster_bytes'] = bpb['bytes_per_sector'] * spc
    usable = bpb['total_sectors'] - bpb['data_start_sector']
    bpb['cluster_count'] = usable // spc if usable > 0 else 0
    if bpb['cluster_count'] < 4085 and not fat32:
        bpb['fat_bits'] = 12
    elif bpb['cluster_count'] < 65525 and not fat32:
        bpb['fat_bits'] = 16
    else:
        bpb['fat_bits'] = 32
    bpb['max_cluster'] = bpb['cluster_count'] + RESERVED_ENTRIES - 1
    if image_bytes is not None:
        bpb['image_bytes'] = image_bytes
        bpb['declared_bytes'] = bpb['total_sectors'] * bpb['bytes_per_sector']
    return bpb


def fat_entry(fat, bpb, index):
    bits = bpb['fat_bits']
    if bits == 12:
        offset = index + index // 2
        value = int.from_bytes(fat[offset:offset + 2], 'little')
        return (value >> 4) & 0x0FFF if index & 1 else value & 0x0FFF
    if bits == 16:
        return u16(fat, index * 2)
    return u32(fat, index * 4) & 0x0FFFFFFF


def classify_entry(value, bpb):
    """Return (kind, next_cluster) for a FAT entry value."""
    bits = bpb['fat_bits']
    eoc = {12: 0xFF8, 16: 0xFFF8, 32: 0x0FFFFFF8}[bits]
    bad = {12: 0xFF7, 16: 0xFFF7, 32: 0x0FFFFFF7}[bits]
    invalid_floor = {12: 0xFF0, 16: 0xFFF0, 32: 0x0FFFFFF0}[bits]
    if value == 0:
        return 'free', None
    if value == bad:
        return 'bad', None
    if value >= eoc:
        return 'eoc', None
    if value >= invalid_floor:
        return 'invalid_reserved', None
    if value < RESERVED_ENTRIES:
        return 'reserved', None
    if value > bpb['max_cluster']:
        return 'out_of_range', None
    return 'next', value


class Volume:
    """Structural access to one FAT volume inside an image."""

    def __init__(self, image, bpb, fat32_bytes=None):
        self.image = image
        self.bpb = bpb
        self.boot = image.read(0, 512)
        self.fats = []
        for index in range(max(bpb['num_fats'], 0)):
            offset = (bpb['reserved_sectors'] + index * bpb['fat_size_sectors']) * bpb['bytes_per_sector']
            self.fats.append(image.read(offset, bpb['fat_size_sectors'] * bpb['bytes_per_sector']))
        self.findings = []

    def report(self, severity, code, detail):
        self.findings.append(dict(severity=severity, code=code, detail=detail))

    def entry(self, cluster, copy=0):
        return fat_entry(self.fats[copy], self.bpb, cluster)

    def cluster_offset(self, cluster):
        return (self.bpb['data_start_sector'] * self.bpb['bytes_per_sector']
                + (cluster - RESERVED_ENTRIES) * self.bpb['cluster_bytes'])

    def chain(self, first, owner):
        """Follow a cluster chain; returns (clusters, problem)."""
        clusters = []
        problem = None
        seen = set()
        cluster = first
        limit = self.bpb['cluster_count'] + RESERVED_ENTRIES + 1
        while True:
            if cluster in seen:
                problem = ('chain_loop', 'cluster %d repeats (owner %s)' % (cluster, owner))
                break
            seen.add(cluster)
            clusters.append(cluster)
            kind, nxt = classify_entry(self.entry(cluster), self.bpb)
            if kind == 'eoc':
                break
            if kind != 'next':
                problem = ('chain_%s' % kind, 'cluster %d -> %s (owner %s)' % (cluster, kind, owner))
                break
            cluster = nxt
            if len(clusters) > limit:
                problem = ('chain_too_long', 'owner %s exceeded %d clusters' % (owner, limit))
                break
        return clusters, problem

    def read_chain(self, clusters, size=None):
        data = bytearray()
        for cluster in clusters:
            data += self.image.read(self.cluster_offset(cluster), self.bpb['cluster_bytes'])
            if size is not None and len(data) >= size:
                break
        return bytes(data[:size]) if size is not None else bytes(data)

    def dir_entries(self, directory_chain):
        """Return (entries, problems) for a directory given its cluster chain."""
        entries = []
        problems = []
        lfn_slots = []
        if directory_chain:
            blob = self.read_chain(directory_chain)
        else:
            blob, root_problem = self.root_directory_bytes()
            if root_problem:
                problems.append(root_problem)
        for index in range(len(blob) // 32):
            raw = blob[index * 32:(index + 1) * 32]
            if raw[0] == 0x00:
                break
            if raw[0] == 0xE5:
                lfn_slots = []
                continue
            attr = raw[11]
            if attr & ATTR_LFN == ATTR_LFN:
                lfn_slots.append((index, raw))
                continue
            entry = dict(
                slot=index, attr=attr,
                short_name=short_name(raw),
                raw_name=raw[0:11],
                first_cluster=u16(raw, 26) | (u16(raw, 20) << 16 if self.bpb['fat_bits'] == 32 else 0),
                size=u32(raw, 28),
                long_name=None, lfn_problem=None,
            )
            if lfn_slots:
                entry['long_name'], entry['lfn_problem'], checksum_ok = assemble_lfn(lfn_slots, raw[0:11])
                if not checksum_ok:
                    problems.append(('lfn_checksum_mismatch',
                                     'LFN slots before slot %d do not match short name %s'
                                     % (index, entry['short_name'])))
                if entry['lfn_problem']:
                    problems.append(('lfn_sequence_problem', entry['lfn_problem']))
            elif attr & ATTR_DIR and entry['short_name'] not in ('.', '..'):
                pass
            lfn_slots = []
            if attr & 0xC0:
                problems.append(('attribute_reserved_bits',
                                 'slot %d attr 0x%02x uses reserved bits' % (index, attr)))
            entries.append(entry)
        if lfn_slots:
            problems.append(('orphan_lfn_slots', '%d LFN slots with no short entry' % len(lfn_slots)))
        return entries, problems

    def root_directory_bytes(self):
        bpb = self.bpb
        if bpb['fat_bits'] == 32:
            clusters, problem = self.chain(bpb['root_cluster'], '<root>')
            return self.read_chain(clusters), problem
        start = (bpb['reserved_sectors'] + bpb['num_fats'] * bpb['fat_size_sectors']) * bpb['bytes_per_sector']
        return self.image.read(start, bpb['root_entries'] * 32), None


def short_name(raw):
    base = raw[0:8].decode('ascii', 'replace').rstrip(' ')
    ext = raw[8:11].decode('ascii', 'replace').rstrip(' ')
    name = base + ('.' + ext if ext else '')
    return name


def lfn_checksum(raw_name):
    value = 0
    for char in raw_name:
        value = (((value & 1) << 7) + (value >> 1) + char) & 0xFF
    return value


def assemble_lfn(slots, raw_name):
    """Assemble long-name slots; returns (name, problem, checksum_ok)."""
    ordered = []
    for _, raw in slots:
        ordered.append((raw[0] & 0x3F, raw[0] & 0x40, raw))
    ordered.sort(key=lambda item: item[0])
    pieces = []
    terminated = False
    for _, _, raw in ordered:
        for offset, length in ((1, 10), (14, 12), (28, 4)):
            chunk = raw[offset:offset + length]
            for index in range(0, len(chunk), 2):
                unit = u16(chunk, index)
                if unit == 0x0000:
                    terminated = True
                    break
                if unit != 0xFFFF:
                    pieces.append(chr(unit))
            if terminated:
                break
        if terminated:
            break
    name = ''.join(pieces)
    problems = []
    if not any(flag for _, flag, _ in ordered):
        problems.append('no last-slot marker (0x40) in %d LFN slots' % len(ordered))
    expected = ordered[0][2][13]
    if any(raw[13] != expected for _, _, raw in ordered):
        problems.append('LFN checksum byte differs between slots')
    checksum = lfn_checksum(raw_name)
    checksum_ok = all(raw[13] == checksum for _, _, raw in ordered)
    if not checksum_ok:
        problems.append('LFN checksum 0x%02X does not match short name 0x%02X'
                        % (expected, checksum))
    return name, '; '.join(problems) if problems else None, checksum_ok


def audit(path, expect=None, image_bytes=None, fat_bytes=None):
    """Audit a FAT volume image; returns the report dict."""
    report = dict(
        audited=False, read_only=True, path=str(path),
        disclaimer='structural audit only; not fsck.fat/dosfsck and not a repair',
        findings=[], verdict=None,
    )
    with Image(path) as image:
        if image.size is None:
            if not image_bytes:
                raise ValueError('raw volume requires --bytes')
            image.size = image_bytes
        elif image_bytes and image.size != image_bytes:
            report['findings'].append(dict(severity='warning', code='image_size_mismatch',
                                           detail='image is %d bytes, expected %d' % (image.size, image_bytes)))
        boot = image.read(0, 512)
        bpb = parse_bpb(boot, image.size)
        volume = Volume(image, bpb)
        findings = volume.findings

        # --- BPB sanity -----------------------------------------------------
        if bpb['boot_signature'] != '55aa':
            findings.append(dict(severity='error', code='boot_signature',
                                 detail='boot sector signature is %s' % bpb['boot_signature']))
        if not (512 <= bpb['bytes_per_sector'] <= 4096 and bpb['bytes_per_sector'] & (bpb['bytes_per_sector'] - 1) == 0):
            findings.append(dict(severity='error', code='bogus_bytes_per_sector',
                                 detail='bytes per sector %d' % bpb['bytes_per_sector']))
        spc = bpb['sectors_per_cluster']
        if spc == 0 or spc & (spc - 1):
            findings.append(dict(severity='error', code='bogus_sectors_per_cluster',
                                 detail='sectors per cluster %d' % spc))
        if bpb['reserved_sectors'] == 0:
            findings.append(dict(severity='error', code='bogus_reserved', detail='reserved sectors 0'))
        if bpb['num_fats'] == 0:
            findings.append(dict(severity='error', code='bogus_fats', detail='number of FATs 0'))
        if bpb['fat_size_sectors'] == 0:
            findings.append(dict(severity='error', code='bogus_fat_size', detail='FAT size 0'))
        if bpb['total_sectors'] == 0:
            findings.append(dict(severity='error', code='bogus_total_sectors', detail='total sectors 0'))
        if bpb['fat_bits'] == 32 and bpb['root_entries']:
            findings.append(dict(severity='warning', code='fat32_root_entries',
                                 detail='FAT32 volume declares %d root entries' % bpb['root_entries']))
        if bpb['fat_bits'] != 32:
            if not bpb['root_entries'] or bpb['root_entries'] % 16:
                findings.append(dict(severity='warning', code='root_entries_alignment',
                                     detail='root entries %d' % bpb['root_entries']))
        declared = bpb['type_string'].strip().upper()
        expected_type = 'FAT%d' % bpb['fat_bits']
        if declared and declared != expected_type:
            findings.append(dict(severity='warning', code='type_string_mismatch',
                                 detail='type string %r but derived FAT%d' % (declared, bpb['fat_bits'])))
        if bpb['cluster_count'] <= 0:
            findings.append(dict(severity='error', code='bogus_cluster_count',
                                 detail='cluster count %d' % bpb['cluster_count']))
        entries_per_sector = bpb['bytes_per_sector'] * 8 // max(bpb['fat_bits'], 1) if bpb['fat_bits'] != 12 else 0
        if entries_per_sector:
            capacity = entries_per_sector * bpb['fat_size_sectors']
            if capacity < bpb['max_cluster'] + 1:
                findings.append(dict(severity='error', code='fat_capacity',
                                     detail='FAT holds %d entries, needs %d' % (capacity, bpb['max_cluster'] + 1)))
        if bpb['declared_bytes'] > image.size:
            findings.append(dict(severity='error', code='volume_larger_than_image',
                                 detail='declares %d bytes, image has %d' % (bpb['declared_bytes'], image.size)))
        elif bpb['declared_bytes'] != image.size:
            findings.append(dict(severity='info', code='volume_smaller_than_image',
                                 detail='declares %d bytes, image has %d trailing bytes'
                                        % (bpb['declared_bytes'], image.size - bpb['declared_bytes'])))

        # --- FAT copies -----------------------------------------------------
        copies = dict(count=len(volume.fats), identical=True, first_difference_byte=None)
        if len(volume.fats) > 1:
            for index in range(1, len(volume.fats)):
                if volume.fats[index] != volume.fats[0]:
                    copies['identical'] = False
                    for offset in range(min(len(volume.fats[0]), len(volume.fats[index]))):
                        if volume.fats[0][offset] != volume.fats[index][offset]:
                            copies['first_difference_byte'] = offset
                            break
                    findings.append(dict(severity='error', code='fat_copies_differ',
                                         detail='FAT0 and FAT%d differ from byte %s'
                                                % (index, copies['first_difference_byte'])))
        for index, fat in enumerate(volume.fats):
            first = fat_entry(fat, bpb, 0)
            expected_media = ({12: 0x0F00, 16: 0xFF00, 32: 0x0FFFFF00}[bpb['fat_bits']] | bpb['media'])
            if first != expected_media:
                findings.append(dict(severity='warning', code='fat0_media_mismatch',
                                     detail='FAT%d[0]=0x%X, media byte 0x%02X implies 0x%X'
                                            % (index, first, bpb['media'], expected_media)))

        # --- walk ----------------------------------------------------------
        referenced = {}
        objects = []
        free_count = 0
        for cluster in range(RESERVED_ENTRIES, bpb['max_cluster'] + 1):
            if classify_entry(volume.entry(cluster), bpb)[0] == 'free':
                free_count += 1
        queue = [('', None)]
        visited_dirs = set()
        directory_budget = 4096
        if bpb['fat_bits'] == 32 and bpb['root_cluster']:
            root_clusters, root_problem = volume.chain(bpb['root_cluster'], '<root>')
            if root_problem:
                findings.append(dict(severity='error', code=root_problem[0], detail=root_problem[1]))
            for cluster in root_clusters:
                referenced.setdefault(cluster, '<root>')
        while queue:
            prefix, chain = queue.pop(0)
            if chain is not None:
                key = tuple(chain)
                if key in visited_dirs:
                    findings.append(dict(severity='error', code='directory_loop',
                                         detail='%s repeats directory cluster chain %s'
                                                % (prefix, key[:8])))
                    continue
                visited_dirs.add(key)
                if len(visited_dirs) > directory_budget:
                    findings.append(dict(severity='error', code='too_many_directories',
                                         detail='stopped after %d directories' % directory_budget))
                    break
            if chain is None:
                blob, problem = volume.root_directory_bytes()
                if problem:
                    findings.append(dict(severity='error', code=problem[0], detail=problem[1]))
                entries, problems = volume.dir_entries(None)
            else:
                entries, problems = volume.dir_entries(chain)
            for code, detail in problems:
                findings.append(dict(severity='warning', code=code, detail='%s: %s' % (prefix, detail)))
            for entry in entries:
                name = entry['long_name'] or entry['short_name']
                path_name = prefix + '/' + name
                if not entry['short_name'].strip() or not name.strip():
                    findings.append(dict(severity='warning', code='degenerate_directory_entry',
                                         detail='%s: slot %d has an empty name (attr 0x%02x, cluster %d)'
                                                % (prefix, entry['slot'], entry['attr'],
                                                   entry['first_cluster'])))
                    continue
                if entry['attr'] & ATTR_VOLUME and not entry['attr'] & ATTR_DIR:
                    objects.append(dict(path=path_name, kind='volume_label', size=0, clusters=[]))
                    continue
                kind = 'dir' if entry['attr'] & ATTR_DIR else 'file'
                if entry['short_name'] in ('.', '..') and kind == 'dir':
                    continue
                if entry['first_cluster'] == 0:
                    if kind == 'file' and entry['size'] > 0:
                        findings.append(dict(severity='error', code='file_without_cluster',
                                             detail='%s has size %d but first cluster 0'
                                                    % (path_name, entry['size'])))
                        objects.append(dict(path=path_name, kind=kind, size=entry['size'], clusters=[],
                                            long_name=entry['long_name'], short_name=entry['short_name']))
                        continue
                    if kind == 'dir':
                        objects.append(dict(path=path_name, kind=kind, size=0, clusters=[],
                                            long_name=entry['long_name'], short_name=entry['short_name']))
                        continue
                    objects.append(dict(path=path_name, kind=kind, size=0, clusters=[],
                                        long_name=entry['long_name'], short_name=entry['short_name']))
                    continue
                chain_clusters, problem = volume.chain(entry['first_cluster'], path_name)
                if problem:
                    findings.append(dict(severity='error', code=problem[0], detail=problem[1]))
                for cluster in chain_clusters:
                    if cluster in referenced:
                        findings.append(dict(severity='error', code='cross_linked_cluster',
                                             detail='cluster %d used by %s and %s'
                                                    % (cluster, referenced[cluster], path_name)))
                    else:
                        referenced[cluster] = path_name
                if kind == 'file':
                    needed = (entry['size'] + bpb['cluster_bytes'] - 1) // bpb['cluster_bytes']
                    if len(chain_clusters) < needed:
                        findings.append(dict(severity='error', code='chain_shorter_than_size',
                                             detail='%s needs %d clusters for %d bytes, chain has %d'
                                                    % (path_name, needed, entry['size'], len(chain_clusters))))
                    elif len(chain_clusters) > needed:
                        findings.append(dict(severity='error', code='chain_longer_than_size',
                                             detail='%s holds %d bytes but chain has %d clusters (needs %d)'
                                                    % (path_name, entry['size'], len(chain_clusters), needed)))
                else:
                    if len(chain_clusters) * bpb['cluster_bytes'] % bpb['cluster_bytes']:
                        findings.append(dict(severity='warning', code='directory_partial_cluster',
                                             detail=path_name))
                objects.append(dict(path=path_name, kind=kind, size=entry['size'],
                                    clusters=chain_clusters, long_name=entry['long_name'],
                                    short_name=entry['short_name'], attr=entry['attr']))
                if kind == 'dir' and len(chain_clusters):
                    queue.append((path_name, chain_clusters))

        lost = [cluster for cluster in range(RESERVED_ENTRIES, bpb['max_cluster'] + 1)
                if classify_entry(volume.entry(cluster), bpb)[0] != 'free' and cluster not in referenced]
        if lost:
            findings.append(dict(severity='warning', code='lost_clusters',
                                 detail='%d allocated clusters are unreachable (first: %s)'
                                        % (len(lost), lost[:16])))
        allocation = dict(
            cluster_count=bpb['cluster_count'], free_clusters=free_count,
            allocated_clusters=bpb['cluster_count'] - free_count,
            referenced_clusters=len(referenced), lost_clusters=len(lost),
            lost_cluster_list=lost[:64],
        )
        # FAT[1]: mount-state flags are sticky-by-design in Linux; report, never verdict.
        fat1 = volume.entry(1)
        allocation['fat1'] = fat1

        # --- optional content verification ---------------------------------
        verification = None
        if expect:
            verification = verify_files(volume, objects, expect)

        errors = [f for f in findings if f['severity'] == 'error']
        warnings = [f for f in findings if f['severity'] == 'warning']
        verdict = 'inconsistent' if errors else ('consistent_with_warnings' if warnings else 'consistent')
        if verification and verification['status'] != 'verified':
            verdict = 'inconsistent' if verification['missing'] or verification['mismatched'] else verdict

        report.update(
            audited=True, verdict=verdict, volume=bpb, fat_copies=copies,
            allocation=allocation, objects=objects, object_count=len(objects),
            findings=findings, content_verification=verification,
            error_count=len(errors), warning_count=len(warnings),
            utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )
    return report


def verify_files(volume, objects, expect):
    """Compare expected {name: {bytes, sha256}} against files found in the volume."""
    wanted = {}
    for name, info in expect.items():
        if isinstance(info, dict):
            wanted[name.lower()] = (info.get('bytes'), info.get('sha256'))
        else:
            wanted[name.lower()] = (None, str(info))
    verified, mismatched, missing, extra = [], [], [], []
    matched = set()
    for item in objects:
        if item['kind'] != 'file':
            continue
        candidates = {item['path'].rsplit('/', 1)[-1].lower()}
        if item.get('long_name'):
            candidates.add(item['long_name'].lower())
        if item.get('short_name'):
            candidates.add(item['short_name'].lower())
        hits = candidates & set(wanted)
        if not hits:
            extra.append(item['path'])
            continue
        name = sorted(hits)[0]
        matched.add(name)
        expected_bytes, expected_sha = wanted[name]
        size_ok = expected_bytes is None or item['size'] == expected_bytes
        digest = hashlib.sha256(volume.read_chain(item['clusters'], item['size'])).hexdigest()
        if size_ok and digest == expected_sha:
            verified.append(dict(name=name, path=item['path'], bytes=item['size'], sha256=digest))
        else:
            mismatched.append(dict(name=name, path=item['path'], bytes_expected=expected_bytes,
                                   bytes_actual=item['size'], sha256_expected=expected_sha,
                                   sha256_actual=digest))
    missing = sorted(name for name in wanted if name not in matched)
    status = 'verified' if not missing and not mismatched else 'failed'
    return dict(status=status, verified=verified, mismatched=mismatched, missing=missing,
                unlisted_files_in_volume=extra, expected_count=len(wanted))


def load_expect(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    for key in ('backup_sha256', 'files', 'expect'):
        if isinstance(data.get(key), dict):
            return data[key]
    if all(isinstance(value, (dict, str)) for value in data.values()):
        return data
    raise ValueError('cannot find a name -> {bytes, sha256} mapping in %s' % path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', help='path to a FAT volume image, or \\\\.\\X: raw volume')
    parser.add_argument('--expect', type=Path, help='JSON mapping of expected file name -> {bytes, sha256}')
    parser.add_argument('--bytes', type=int, help='size of a raw volume in bytes (required for \\\\.\\X:)')
    parser.add_argument('--report', type=Path, help='write the full report JSON here')
    parser.add_argument('--fat-bytes', type=int, help='accepted for symmetry; unused')
    args = parser.parse_args(argv)
    expect = load_expect(args.expect) if args.expect else None
    try:
        report = audit(args.image, expect=expect, image_bytes=args.bytes)
    except Exception as exc:
        print(json.dumps(dict(audited=False, error_type=type(exc).__name__, error=str(exc)), indent=2))
        return 2
    if args.report:
        args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
    summary = dict(verdict=report['verdict'], fat_bits=report['volume']['fat_bits'],
                   cluster_count=report['volume']['cluster_count'],
                   label=report['volume']['label'].strip(),
                   objects=report['object_count'], errors=report['error_count'],
                   warnings=report['warning_count'],
                   findings=[(f['severity'], f['code']) for f in report['findings']],
                   content=report['content_verification']['status'] if report['content_verification'] else None)
    print(json.dumps(summary, indent=2))
    return 0 if report['verdict'] != 'inconsistent' else 1


if __name__ == '__main__':
    raise SystemExit(main())
