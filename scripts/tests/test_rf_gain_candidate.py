"""The observer daemon is accepted only with exact native build evidence."""
import hashlib
import json

import pytest

from scripts import rf_environment_candidate as C


@pytest.fixture
def native(tmp_path):
    source=tmp_path/'src';source.mkdir()
    for sub in ('vendor','test'):(source/sub).mkdir()
    (source/'rf_version.h').write_text('#define RF_DAEMON_VERSION "1.0.1"\n#define RF_PROTOCOL_MAJOR 1u\n#define RF_PROTOCOL_MINOR 3u\n')
    (source/'rf_core.c').write_text('checked reads')
    (source/'Makefile').write_text('all: rf_daemon')
    (source/'test'/'test_rf_daemon.c').write_text('explicit opt-in')
    binary=tmp_path/'rf_daemon';elf=bytearray(64)
    elf[:6]=b'\x7fELF\x01\x01';elf[18:20]=(40).to_bytes(2,'little');binary.write_bytes(elf)
    receipt=dict(architecture='armv7l',hardware_touched=False,
        sha256=hashlib.sha256(elf).hexdigest(),
        source_sha256={str(p.relative_to(source)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source.rglob('*') if p.is_file()},
        targets=['all','host-test','host-test-core','host-test-iq','host-test-protocol','vendor-integrity'])
    evidence=binary.with_suffix('.build.json');evidence.write_text(json.dumps(receipt))
    binary.with_suffix('.build.log').write_text('BUILD_RC=0\nvendor integrity: 10 files passed\n')
    return source,binary,evidence,receipt


def test_exact_native_observer_is_accepted(native):
    source,binary,evidence,_=native
    assert C.verify_observer_build(binary,evidence,source)['daemon_version']=='1.0.1'


def test_windows_receipt_keys_are_normalized_without_weakening_hashes(native):
    source,binary,evidence,receipt=native
    receipt['source_sha256']={k.replace('/','\\'):v for k,v in receipt['source_sha256'].items()}
    evidence.write_text(json.dumps(receipt))
    assert C.verify_observer_build(binary,evidence,source)['daemon_version']=='1.0.1'


@pytest.mark.parametrize('field,value',[('architecture','x86_64'),('hardware_touched',True),
    ('sha256','0'*64),('targets',['all']),('source_sha256',{})])
def test_incomplete_or_wrong_build_is_rejected(native,field,value):
    source,binary,evidence,receipt=native
    receipt[field]=value;evidence.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):C.verify_observer_build(binary,evidence,source)


def test_changed_source_is_rejected(native):
    source,binary,evidence,_=native
    (source/'rf_core.c').write_text('stale build')
    with pytest.raises(ValueError):C.verify_observer_build(binary,evidence,source)


def test_self_reported_arm_without_arm_elf_is_rejected(native):
    source,binary,evidence,receipt=native
    elf=bytearray(binary.read_bytes());elf[18:20]=(62).to_bytes(2,'little');binary.write_bytes(elf)
    receipt['sha256']=hashlib.sha256(elf).hexdigest();evidence.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):C.verify_observer_build(binary,evidence,source)


def test_failed_build_log_is_rejected(native):
    source,binary,evidence,_=native
    binary.with_suffix('.build.log').write_text('BUILD_RC=2')
    with pytest.raises(ValueError):C.verify_observer_build(binary,evidence,source)


def test_wrong_protocol_revision_is_rejected_even_with_matching_source_hash(native):
    source,binary,evidence,receipt=native
    header=source/'rf_version.h';header.write_text(header.read_text().replace('MINOR 3u','MINOR 2u'))
    receipt['source_sha256']['rf_version.h']=hashlib.sha256(header.read_bytes()).hexdigest()
    evidence.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):C.verify_observer_build(binary,evidence,source)


def test_explicit_timing_daemon_and_minor_pair_is_accepted(native):
    source,binary,evidence,receipt=native
    header=source/'rf_version.h'
    header.write_text(header.read_text().replace('"1.0.1"','"1.0.2"').replace('MINOR 3u','MINOR 4u'))
    receipt['source_sha256']['rf_version.h']=hashlib.sha256(header.read_bytes()).hexdigest()
    evidence.write_text(json.dumps(receipt))
    verified=C.verify_observer_build(binary,evidence,source)
    assert verified['daemon_version']=='1.0.2' and verified['protocol_minor']==4 and verified['protocol_major']==1


def test_settle_wait_daemon_and_minor_pair_is_accepted(native):
    source,binary,evidence,receipt=native
    header=source/'rf_version.h'
    header.write_text(header.read_text().replace('"1.0.1"','"1.0.3"').replace('MINOR 3u','MINOR 5u'))
    receipt['source_sha256']['rf_version.h']=hashlib.sha256(header.read_bytes()).hexdigest()
    evidence.write_text(json.dumps(receipt))
    verified=C.verify_observer_build(binary,evidence,source)
    assert verified['daemon_version']=='1.0.3' and verified['protocol_minor']==5 and verified['protocol_major']==1


def test_timing_daemon_with_old_minor_is_rejected(native):
    source,binary,evidence,receipt=native
    header=source/'rf_version.h';header.write_text(header.read_text().replace('"1.0.1"','"1.0.2"'))
    receipt['source_sha256']['rf_version.h']=hashlib.sha256(header.read_bytes()).hexdigest()
    evidence.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):C.verify_observer_build(binary,evidence,source)


def test_settle_wait_daemon_with_a_stale_minor_is_rejected(native):
    """1.0.3 must not be accepted while it still declares the minor-4 revision."""
    source,binary,evidence,receipt=native
    header=source/'rf_version.h';header.write_text(header.read_text().replace('"1.0.1"','"1.0.3"'))
    receipt['source_sha256']['rf_version.h']=hashlib.sha256(header.read_bytes()).hexdigest()
    evidence.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):C.verify_observer_build(binary,evidence,source)


def _declare(native,version,minor):
    source,binary,evidence,receipt=native
    header=source/'rf_version.h'
    header.write_text('#define RF_DAEMON_VERSION "%s"\n#define RF_PROTOCOL_MAJOR 1u\n'
                      '#define RF_PROTOCOL_MINOR %du\n' % (version,minor))
    receipt['source_sha256']['rf_version.h']=hashlib.sha256(header.read_bytes()).hexdigest()
    evidence.write_text(json.dumps(receipt))
    return source,binary,evidence


def test_headroom_daemon_and_minor_pair_is_accepted(native):
    """The HEADROOM-1 pair is the newest entry in the supported table."""
    source,binary,evidence=_declare(native,'1.0.4',6)
    verified=C.verify_observer_build(binary,evidence,source)
    assert (verified['daemon_version'],verified['protocol_major'],
            verified['protocol_minor'])==('1.0.4',1,6)


@pytest.mark.parametrize('version,minor',[
    ('1.0.4',5),   # the headroom daemon still declaring the settle-wait revision
    ('1.0.5',6),   # a version the table does not list at all
    ('1.0.4',7),   # a revision past the reviewed one
])
def test_a_daemon_pair_the_table_does_not_list_is_rejected(native,version,minor):
    """The table is the one place that says which pair a receipt may claim."""
    source,binary,evidence=_declare(native,version,minor)
    with pytest.raises(ValueError):C.verify_observer_build(binary,evidence,source)


@pytest.mark.parametrize('version,minor',[
    ('1.0.1',3),('1.0.2',4),('1.0.3',5),('1.0.4',6),
])
def test_every_reviewed_pair_in_the_table_is_still_accepted(native,version,minor):
    """One row per reviewed pair: the older ones stay verifiable, they are not
    replaced by the newest.  A table that only knows the current version would
    make every already-packaged candidate unverifiable."""
    source,binary,evidence=_declare(native,version,minor)
    assert C.verify_observer_build(binary,evidence,source)['protocol_minor']==minor
