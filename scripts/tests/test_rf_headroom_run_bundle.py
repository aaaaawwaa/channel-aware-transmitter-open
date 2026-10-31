"""P1 板端 HEADROOM 运行包：红态契约测试（TDD red，判据来源 = 封装规格 §1-§7/§9）。

判据来源（唯一，不得放宽、不得改写）
------------------------------------
`docs/superpowers/specs/2026-10-13-headroom-board-run-package-design.md`：
§1 运行包来源与边界、§2 板端目录布局与权限、§3 受控导入路径（B1）、
§4 上传摘要守卫、§5 启动参数、§6 单次派发与防重放（B3）、§7 结局枚举、
§9 测试计划（干净子进程 / 负控集合 / 变异 / 不得修改期待值求全绿）。

本文件只写判据，不写实现；被测产品源码与本文件之外的任何文件都不改。当前仓库尚未
实现这些契约，因此本文件按设计**应为红**：每个用例的 docstring 写明所判规格小节与判据，
断言消息写出"期望什么/实际什么"。若某个用例意外通过（契约已满足或测试太弱），必须在
`evidence/rf-teaching/p1-run-bundle-red-2026-10-14/RED-NOTES.md` 中如实列出，
不允许改断言把它变红。

红态即固定的接口（规格未命名的部分由测试与实现共同固定，§1.3 的"实现阶段与测试一起固定"）
--------------------------------------------------------------------------------------
* 打包/复核路径：`scripts/rf_headroom_run_bundle.py`
  - `BUNDLE_MEMBERS`：§1.3 的成员清单（bundle 内相对 POSIX 路径）
  - `build_run_bundle(source_root, destination, *, source_commit, builder=None, runner_version=None)`
    产出上传布局与 `bundle-receipt.json`，返回 receipt
  - `verify_run_bundle(directory)` 复核并返回 receipt；不符抛 `BundleRefused`
  - `BundleRefused`：复核失败的具名异常
* 板端入口：`scripts/rf_headroom_research_run.py` 既有的 `build_parser()`/`main()`
  CLI（§5 的固定参数、§7 的终态经既有 `HEADROOM_RUN <json>` 标准输出行观察）
* 单次派发：`rf_headroom_research_run.create_dispatch_marker(dispatch_dir, attempt_id, document)`
  （O_EXCL 语义；目标已存在抛 `FileExistsError`）
* 板端账本：`rf_headroom_research_run.BoardAuthorizationLedger(ledger_dir)`
  （`.path` == `<ledger_dir>/authorization.jsonl`；`.refusal(nonce)`；`.consume(**fields)`）
* `bundle-receipt.json` 固定字段：`runner_version`、`source_commit`、`builder`、
  `members`（成员路径 -> {sha256, bytes}）；额外字段容忍。

覆盖不到的判据（Windows 限制，红态不伪装覆盖）
--------------------------------------------
§2 的 `root:root 0700/0600` 权限与属主在 Windows 上不可观测，本文件不断言模式位；
§8 的只读观察会话、§7 中"研究已执行"的 UNKNOWN 分支需要真板，不在本轮离线红态内。
"""

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[2]
RUNNER_FILE = REPO / 'scripts' / 'rf_headroom_research_run.py'
CHECKER_FILE = REPO / 'scripts' / 'rf_teaching_install_state.py'
RUN_BUNDLE_FILE = REPO / 'scripts' / 'rf_headroom_run_bundle.py'
SEALED_RELEASE = (REPO / 'evidence/rf-teaching/s2-headroom-candidate2-2026-10-17'
                  / 'candidate' / 'releases' / 'e66197ed79131b73aa4f47804487c053')

# §1.3：运行包成员（初版清单；scripts/__init__.py 使板端可按 scripts.<name> 导入）
BUNDLE_MEMBERS = ('scripts/__init__.py',
                  'scripts/rf_headroom_research_run.py',
                  'scripts/rf_teaching_install_state.py')
GENERATED_INIT = '"""Board-side package marker for the HEADROOM run bundle (generated)."""\n'

# §2/§5：板端持久状态根与持久账本目录默认值
PERSISTENT_ROOT = '/var/lib/rf-headroom-research'
LEDGER_DIR_DEFAULT = PERSISTENT_ROOT + '/ledger'
QUALIFIED_RELEASE = 'e66197ed79131b73aa4f47804487c053'

SESSION_ID = '0123456789abcdef0123456789abcdef'
ATTEMPT_ID = 'fedcba9876543210fedcba9876543210'
OTHER_ATTEMPT_ID = '00112233445566778899aabbccddeeff'
AUTHORIZATION_ID = 'aabbccddeeff00112233445566778899'
NONCE = 'a1' * 16
OTHER_NONCE = 'b2' * 16
WIRING_STATEMENT = 'TX1->RX1 physical path present'
SOURCE_COMMIT = '0' * 40
BUILDER = 'pytest-red-contract'
OUTCOME_PREFIX = 'HEADROOM_RUN '

# §7：板端包新增的具名终态（含由停止标签生成的固定取消终态）
SECTION_7_LABELS = (
    'RUN_REFUSED_BUNDLE',
    'RUN_REFUSED_IMPORT',
    'RUN_REFUSED_AUTHORIZATION',
    'RUN_REFUSED_CAPABILITY',
    'RUN_UNKNOWN_READBACK_INCOMPLETE',
    'RUN_UNKNOWN_REBOOT',
    'RUN_ABORTED_STORAGE_FAULT',
    'RUN_ABORTED_PREFLIGHT_UNKNOWN',
    'RUN_STOPPED_CANCELLED',
)
# §5：规格点名禁止的放宽门开关
FORBIDDEN_SWITCHES = ('--force', '--retry', '--ignore-wiring-age', '--skip-preflight')


# ------------------------------------------------------------------ 小工具
def _norm(value):
    """大小写/分隔符无关的路径比较键（Windows 上 tmp 短名与长名并存）。"""
    return os.path.normcase(os.path.normpath(str(value)))


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _tree_digest(root):
    root = Path(root)
    return {str(path.relative_to(root)).replace('\\', '/'): _sha256(path)
            for path in sorted(root.rglob('*')) if path.is_file()}


def _load_by_path(path, name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None, (
        '期望：能按路径加载 %s；实际：importlib 无法为它建立 spec' % path)
    module = importlib.util.module_from_spec(spec)
    # 按路径加载的模块仍要登记：模块内的 dataclass 会通过 sys.modules 解析注解。
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _runner():
    """仓库内的被测 runner（§6 接口与 §7 常量的来源）。"""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from scripts import rf_headroom_research_run as module
    return module


def _attr(module, name, section, related=''):
    """取模块属性；缺失时以"期望/实际"的形式失败，而不是抛出 AttributeError。"""
    if hasattr(module, name):
        return getattr(module, name)
    near = sorted(item for item in vars(module)
                  if related and related.lower() in item.lower())
    raise AssertionError(
        '期望：%s 提供 %s（规格 %s）；实际：模块没有这个属性；与 %r 相近的现有名字：%s'
        % (getattr(module, '__name__', module), name, section, related, near or '无'))


def _bundle_module():
    assert RUN_BUNDLE_FILE.is_file(), (
        '期望：§4 的打包/复核路径存在于 scripts/rf_headroom_run_bundle.py；'
        '实际：文件不存在（%s）' % RUN_BUNDLE_FILE)
    module = _load_by_path(RUN_BUNDLE_FILE, 'run_bundle_under_test')
    for name in ('BUNDLE_MEMBERS', 'build_run_bundle', 'verify_run_bundle', 'BundleRefused'):
        _attr(module, name, '§1.3/§1.4/§4', related='bundle')
    return module


def _runner_version_from_source(path):
    """读一个 runner 副本里的 RUNNER_VERSION（§5：与 bundle-receipt.json 绑定）。"""
    tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == 'RUNNER_VERSION'
                and isinstance(node.value, ast.Constant)
                and type(node.value.value) is str):
            return node.value.value
    raise AssertionError('期望：runner 副本定义 RUNNER_VERSION 字符串常量（§5）；'
                         '实际：%s 里没有该赋值' % path)


# ------------------------------------------------- 板端布局与运行包（测试本地构造）
def _write_run_bundle(destination, *, runner_version=None, source_commit=SOURCE_COMMIT,
                      builder=BUILDER):
    """按 §1.3/§1.4 的固定字段构造一个运行包（红态自建，不依赖被测打包器）。"""
    destination = Path(destination)
    (destination / 'scripts').mkdir(parents=True, exist_ok=False)
    (destination / 'scripts' / '__init__.py').write_text(GENERATED_INIT, encoding='utf-8')
    for member in BUNDLE_MEMBERS[1:]:
        shutil.copy2(REPO / member, destination / member)
    version = runner_version or _runner_version_from_source(
        destination / 'scripts' / 'rf_headroom_research_run.py')
    receipt = dict(
        runner_version=version,
        source_commit=source_commit,
        builder=builder,
        members={member: dict(sha256=_sha256(destination / member),
                              bytes=(destination / member).stat().st_size)
                 for member in BUNDLE_MEMBERS})
    (destination / 'bundle-receipt.json').write_text(
        json.dumps(receipt, indent=2, sort_keys=True), encoding='utf-8')
    return receipt


def _board_layout(tmp_path, *, name='board', release=None, bundle=None):
    """§2 的临时"板端布局"：运行包 + 持久状态根 + 操作者 token；仓库树不参与。"""
    root = Path(tmp_path) / name
    root.mkdir(parents=True, exist_ok=True)
    bundle_root = Path(bundle) if bundle is not None else root / 'bundle'
    if bundle is None:
        _write_run_bundle(bundle_root)
    state = root / 'var-lib-rf-headroom-research'
    for sub in ('ledger', 'dispatch', 'evidence', 'observation'):
        (state / sub).mkdir(parents=True, exist_ok=True)
    token = root / 'operator.token'
    token.write_text('one-shot-operator-token\n', encoding='utf-8')
    return dict(root=root, bundle=bundle_root, state=state, token=token,
                authorization=root / 'authorization.json', release=release)


def _entry_args(layout, release, authorization=None, attempt_id=ATTEMPT_ID):
    """§5 的固定启动参数（+ 既有受审输入 --wiring-confirmed，§1 声明不改安全面）。"""
    receipt = json.loads((layout['bundle'] / 'bundle-receipt.json').read_text(encoding='utf-8'))
    return [
        'run',
        '--session-id', SESSION_ID,
        '--authorization', str(authorization or layout['authorization']),
        '--token-path', str(layout['token']),
        '--wiring-confirmed', WIRING_STATEMENT,
        '--ledger-dir', str(layout['state'] / 'ledger'),
        '--evidence-root', str(layout['state'] / 'evidence'),
        '--observation-dir', str(layout['state'] / 'observation'),
        '--runner-version', str(receipt['runner_version']),
        '--release-directory', str(release),
        '--attempt-id', attempt_id,
    ]


def _child_env():
    return {**os.environ, 'PYTHONPATH': '', 'PYTHONIOENCODING': 'utf-8'}


def _run_entry(layout, release, authorization=None, attempt_id=ATTEMPT_ID, timeout=180):
    entry = layout['bundle'] / 'scripts' / 'rf_headroom_research_run.py'
    command = [sys.executable, '-B', str(entry)] + _entry_args(
        layout, release, authorization=authorization, attempt_id=attempt_id)
    proc = subprocess.run(command, cwd=str(layout['root']), env=_child_env(),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding='utf-8', errors='replace', timeout=timeout)
    return dict(exit=proc.returncode, stdout=proc.stdout or '', stderr=proc.stderr or '',
                command=command)


def _outcome(stdout):
    """既有接口：`HEADROOM_RUN <json>` 一行报告终态。"""
    for line in (stdout or '').splitlines():
        if line.startswith(OUTCOME_PREFIX):
            try:
                return json.loads(line[len(OUTCOME_PREFIX):])
            except ValueError:
                return None
    return None


PROBE_SOURCE = r'''
import builtins
import json
import os
import runpy
import sys


def _under(path, root):
    try:
        return os.path.normcase(os.path.abspath(path)).startswith(
            os.path.normcase(os.path.abspath(root)) + os.sep)
    except (TypeError, ValueError):
        return False


def main():
    entry, repository, layout_root, out_path = sys.argv[1:5]
    sys.argv = [entry] + sys.argv[5:]
    code = None
    imported = []
    real_import = builtins.__import__

    def spy(name, *args, **kwargs):
        module = real_import(name, *args, **kwargs)
        imported.append(name)
        return module

    builtins.__import__ = spy
    try:
        runpy.run_path(entry, run_name='__main__')
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else repr(exc.code)
    except BaseException as exc:                      # noqa: BLE001
        code = 'EXCEPTION:%s:%s' % (type(exc).__name__, str(exc)[:400])
    finally:
        builtins.__import__ = real_import
    payload = dict(
        code=code,
        path=list(sys.path),
        imports=sorted(set(imported)),
        repository_modules=sorted(
            name for name, module in list(sys.modules.items())
            if _under(getattr(module, '__file__', None), repository)
            and not _under(getattr(module, '__file__', None), layout_root)))
    with open(out_path, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle)


main()
'''


def _run_import_probe(layout, release, timeout=180):
    """在干净子进程里执行入口并回报 sys.path / 从仓库树加载过的模块（§3.4）。"""
    probe_file = layout['root'] / 'import-probe.py'
    out_file = layout['root'] / 'import-probe.json'
    probe_file.write_text(PROBE_SOURCE, encoding='utf-8')
    entry = layout['bundle'] / 'scripts' / 'rf_headroom_research_run.py'
    command = ([sys.executable, '-B', str(probe_file), str(entry), str(REPO),
                str(layout['root']), str(out_file)] + _entry_args(layout, release))
    proc = subprocess.run(command, cwd=str(layout['root']), env=_child_env(),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding='utf-8', errors='replace', timeout=timeout)
    assert out_file.is_file(), (
        '期望：探针在干净子进程里跑完入口并写出 %s；实际：文件不存在；'
        'stdout=%r；stderr=%r' % (out_file, proc.stdout[-2000:], proc.stderr[-2000:]))
    payload = json.loads(out_file.read_text(encoding='utf-8'))
    payload['stdout'] = proc.stdout or ''
    payload['stderr'] = proc.stderr or ''
    payload['command'] = command
    return payload


def _parser_surface():
    """§5：取出 `run` 子解析器与全部选项串/帮助文本（含子命令）。"""
    module = _runner()
    parser = _attr(module, 'build_parser', '§5', related='parser')()
    subparsers = [action for action in parser._actions
                  if isinstance(action, argparse._SubParsersAction)]
    assert subparsers, ('期望：入口用子命令暴露动作（既有 run/simulate，§5 的固定参数挂在 '
                        'run 上）；实际：顶层解析器没有子解析器')
    choices = subparsers[0].choices
    assert 'run' in choices, ('期望：§5 的板端入口是 run 子命令；实际：只有 %s' % sorted(choices))
    options, helps = set(), {}
    for action in list(parser._actions) + [item for sub in choices.values()
                                           for item in sub._actions]:
        for option in action.option_strings:
            options.add(option)
            helps[option] = action.help or ''
    return choices['run'], options, helps


def _parser_default(run_parser, dest, flags):
    """取参数默认值；dest 不存在时以"期望/实际"失败，而不是抛 KeyError。"""
    try:
        return run_parser.get_default(dest)
    except KeyError:
        existing = sorted(action.dest for action in run_parser._actions)
        raise AssertionError(
            '期望：%s 的参数目标名为 %r（§5）；实际：解析器没有这个 dest；现有 dest=%r'
            % (flags, dest, existing))


# =============================================================== §3 受控导入路径
def test_section_3_a_clean_board_layout_resolves_in_order_and_refuses_by_name(tmp_path):
    """§3 步骤 1-3 + §7 `RUN_REFUSED_IMPORT` + §9.1：干净布局下的顺序与具名结局。

    布局内没有仓库树、没有 pynq_overlay/sw、没有隐式 scripts 包；发布目录是不可核验的
    空壳，授权工件故意缺失。若入口先做 G0 授权判定、再核验发布身份，就会得到授权类
    结局；本用例据此判"发布身份先于导入核验"（§3 步骤 2），且失败必须归入 §7 的
    `RUN_REFUSED_IMPORT`（研究未执行、板卡零写入），而不是裸 traceback 或 argparse 退出。
    """
    release = tmp_path / 'releases' / ('f' * 32)
    release.mkdir(parents=True)
    (release / 'merged_release_manifest.json').write_text('{}', encoding='utf-8')
    layout = _board_layout(tmp_path, release=release)

    result = _run_entry(layout, release)
    assert result['exit'] != 0, (
        '期望：具名拒绝时退出码非零（§7；既有 EXIT_NOT_STARTED=2）；实际：exit=%r；'
        'stdout=%r；stderr=%r' % (result['exit'], result['stdout'][-1500:],
                                  result['stderr'][-1500:]))
    document = _outcome(result['stdout'])
    assert document is not None, (
        '期望：入口按既有接口打印一行 "HEADROOM_RUN <json>"（§7 终态观察点）；'
        '实际：stdout 中没有该行：%r；stderr=%r'
        % (result['stdout'][-1500:], result['stderr'][-1500:]))
    assert document.get('terminal') == 'RUN_REFUSED_IMPORT', (
        '期望：发布身份不可核验（§3 步骤 2 失败）→ §7 具名结局 RUN_REFUSED_IMPORT；'
        '实际：terminal=%r；reason=%r' % (document.get('terminal'), document.get('reason')))
    assert document.get('retry_allowed') is False, (
        '期望：具名拒绝一律 retry_allowed=False（既有 runner 终态契约）；实际：%r'
        % document.get('retry_allowed'))


def test_section_3_sys_path_carries_only_the_bundle_and_the_verified_release(tmp_path):
    """§3 步骤 1/4 + §1.1 + §9.4 的"import 兜底搜索"变异：受控搜索路径、无仓库兜底。

    在干净子进程里跑入口（发布目录是密封候选的副本，身份可核验），随后检查：仓库的
    产品源码路径（仓库根、scripts、pynq_overlay[/sw]、rf_overlay[/sw]）不在 sys.path 上、
    运行包根与发布实路径在 sys.path 上、没有任何模块从仓库树（板端布局根之外）加载；
    并在源码上判"不得有 importlib 按名搜索兜底或 sys.path.append 任意目录"。
    （pytest 的 --basetemp 位于仓库内，故按"产品源码路径不得可导入"而非前缀判定。）
    """
    assert SEALED_RELEASE.is_dir(), (
        '期望：密封候选发布目录存在（本用例的受控发布副本来源）；实际：%s 不存在'
        % SEALED_RELEASE)
    release = tmp_path / 'releases' / QUALIFIED_RELEASE
    shutil.copytree(str(SEALED_RELEASE), str(release))
    layout = _board_layout(tmp_path, release=release)

    probe = _run_import_probe(layout, release)
    paths = [str(item) for item in probe['path'] if isinstance(item, str)]
    normalized = [_norm(item) for item in paths]

    forbidden = {_norm(REPO), _norm(REPO / 'scripts'), _norm(REPO / 'pynq_overlay'),
                 _norm(REPO / 'pynq_overlay' / 'sw'), _norm(REPO / 'rf_overlay'),
                 _norm(REPO / 'rf_overlay' / 'sw')}
    repo_entries = sorted(item for item in paths if _norm(item) in forbidden)
    assert not repo_entries, (
        '期望：仓库的产品源码路径（%s）不出现在板端 sys.path 上（§3.1 只允许运行包根与'
        '已核验发布软件目录实路径；§3.4 禁止从仓库路径导入）；实际：命中的条目=%r；'
        'sys.path=%r' % (sorted(forbidden), repo_entries, paths))
    assert _norm(layout['bundle']) in normalized, (
        '期望：运行包根在 sys.path 上，使 scripts.<name> 可导入（§3 步骤 1）；'
        '实际：sys.path=%r；运行包根=%s' % (paths, layout['bundle']))
    release_key = _norm(release)
    release_present = (release_key in normalized
                       or any(os.path.basename(item) == QUALIFIED_RELEASE
                              for item in normalized))
    assert release_present, (
        '期望：已核验发布的软件目录实路径在 sys.path 上（§3 步骤 1/2）；'
        '实际：sys.path=%r；发布目录=%s；从仓库树加载的模块=%r；子进程退出码=%r；'
        'stderr=%r' % (paths, release, probe['repository_modules'], probe['code'],
                       probe['stderr'][-1200:]))
    assert probe['repository_modules'] == [], (
        '期望：干净布局下没有任何模块从仓库树加载（§3.4 禁止从仓库路径导入、禁止把'
        '发布模块复制进运行包）；实际：%r' % probe['repository_modules'])

    offenders = _fallback_search_offenders(RUNNER_FILE.read_text(encoding='utf-8'))
    assert offenders == [], (
        '期望：runner 不含 sys.path.append(任意目录) 或 importlib.import_module() 的按名'
        '搜索兜底（§3.4；§9.4 要求该变异被抓）；实际：%r' % offenders)


def _fallback_search_offenders(source):
    """§3.4 的静态判据：按名搜索兜底与任意 sys.path 追加。"""
    tree = ast.parse(source)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func
        dotted = None
        if isinstance(owner.value, ast.Name):
            dotted = '%s.%s' % (owner.value.id, owner.attr)
        elif isinstance(owner.value, ast.Attribute) and isinstance(owner.value.value, ast.Name):
            dotted = '%s.%s.%s' % (owner.value.value.id, owner.value.attr, owner.attr)
        if dotted == 'sys.path.append':
            offenders.append('sys.path.append at line %d' % node.lineno)
        if dotted == 'importlib.import_module':
            offenders.append('importlib.import_module at line %d' % node.lineno)
    return offenders


# =============================================================== §4 上传摘要守卫
def test_section_4_the_run_bundle_is_built_and_verified_member_by_member(tmp_path):
    """§1.3/§1.4/§4：打包路径产出并复核逐成员 SHA-256 与字节数的 bundle-receipt.json。

    正面判据：成员清单等于 §1.3；receipt 的 members 与磁盘上独立重算的 SHA-256/字节数
    一致；`source_commit`/`builder`/`runner_version` 落盘（§5 的 --runner-version 由此绑定）；
    复核路径接受自己产出的包；板端可按 `scripts.<name>` 导入（scripts/__init__.py 的存在理由）。
    """
    bundle = _bundle_module()
    members = tuple(bundle.BUNDLE_MEMBERS)
    assert members == BUNDLE_MEMBERS, (
        '期望：§1.3 的成员清单 %r；实际：%r' % (BUNDLE_MEMBERS, members))

    destination = tmp_path / 'run-bundle'
    receipt = bundle.build_run_bundle(REPO, destination, source_commit=SOURCE_COMMIT,
                                      builder=BUILDER)
    written = json.loads((destination / 'bundle-receipt.json').read_text(encoding='utf-8'))
    assert written == receipt, (
        '期望：返回的 receipt 与写入 bundle-receipt.json 的内容一致（§1.4 单一构建记录）；'
        '实际：返回=%r；落盘=%r' % (receipt, written))
    assert sorted(receipt.get('members') or {}) == sorted(BUNDLE_MEMBERS), (
        '期望：receipt.members 恰好覆盖 §1.3 的成员；实际：%r'
        % sorted(receipt.get('members') or {}))
    for member in BUNDLE_MEMBERS:
        path = destination / member
        assert path.is_file(), (
            '期望：成员 %s 出现在运行包里（§1.3；scripts/__init__.py 需由打包路径生成，'
            '仓库中没有该文件）；实际：不存在' % member)
        entry = receipt['members'][member]
        assert entry['sha256'] == _sha256(path), (
            '期望：%s 的 SHA-256 等于独立重算值（§4 逐成员摘要）；实际：receipt=%s，'
            '磁盘=%s' % (member, entry['sha256'], _sha256(path)))
        assert entry['bytes'] == path.stat().st_size, (
            '期望：%s 的字节数等于磁盘值（§4）；实际：receipt=%r，磁盘=%r'
            % (member, entry['bytes'], path.stat().st_size))
    assert receipt.get('source_commit') == SOURCE_COMMIT, (
        '期望：receipt 记录来源提交号（§1.1/§1.4）；实际：%r' % receipt.get('source_commit'))
    assert receipt.get('builder') == BUILDER, (
        '期望：receipt 记录构建者（§1.4）；实际：%r' % receipt.get('builder'))
    assert receipt.get('runner_version') == _runner_version_from_source(
        destination / 'scripts' / 'rf_headroom_research_run.py'), (
        '期望：receipt.runner_version 与包内 runner 的 RUNNER_VERSION 绑定（§5）；'
        '实际：%r' % receipt.get('runner_version'))

    verified = bundle.verify_run_bundle(destination)
    assert verified.get('members') == receipt['members'], (
        '期望：复核路径接受自己产出的包并回读同一 members（§4）；实际：%r'
        % (verified.get('members') if isinstance(verified, dict) else verified))

    check = subprocess.run(
        [sys.executable, '-B', '-c',
         'import scripts.rf_headroom_research_run, scripts.rf_teaching_install_state;'
         ' print("BUNDLE-IMPORTS-OK")'],
        cwd=str(destination), env=_child_env(), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, encoding='utf-8', errors='replace', timeout=180)
    assert 'BUNDLE-IMPORTS-OK' in (check.stdout or ''), (
        '期望：运行包内可按 scripts.<name> 导入（§1.3；§3 步骤 1 的前提）；'
        '实际：exit=%r；stdout=%r；stderr=%r'
        % (check.returncode, check.stdout, check.stderr[-1500:]))


def test_section_4_a_missing_member_is_refused_by_name(tmp_path):
    """§4 负控（成员缺失）+ §7 `RUN_REFUSED_BUNDLE`：复核必须具名拒绝。

    §9.5：pytest.raises 必须 match 到本用例名字所指的那条消息，不允许"任意异常都算过"。
    """
    bundle = _bundle_module()
    destination = tmp_path / 'run-bundle'
    bundle.build_run_bundle(REPO, destination, source_commit=SOURCE_COMMIT, builder=BUILDER)
    missing = 'scripts/rf_teaching_install_state.py'
    (destination / missing).unlink()

    with pytest.raises(bundle.BundleRefused, match='is missing from the bundle'):
        bundle.verify_run_bundle(destination)


def test_section_4_an_undeclared_extra_member_is_refused_by_name(tmp_path):
    """§4 负控（成员多余）+ §1.3（运行包只含固定成员）：多一个文件也必须拒绝。"""
    bundle = _bundle_module()
    destination = tmp_path / 'run-bundle'
    bundle.build_run_bundle(REPO, destination, source_commit=SOURCE_COMMIT, builder=BUILDER)
    extra = destination / 'scripts' / 'rf_headroom_deploy.py'
    extra.write_text('# undeclared member: the board has no such module' + chr(10),
                    encoding='utf-8')

    with pytest.raises(bundle.BundleRefused, match='is not a declared bundle member'):
        bundle.verify_run_bundle(destination)


def test_section_4_a_digest_or_byte_mismatch_is_refused_by_name(tmp_path):
    """§4 负控（摘要/字节数不符）：改动成员内容、或改动 receipt 的字节数都要被拒绝。"""
    bundle = _bundle_module()
    destination = tmp_path / 'run-bundle'
    bundle.build_run_bundle(REPO, destination, source_commit=SOURCE_COMMIT, builder=BUILDER)
    member = destination / 'scripts' / 'rf_headroom_research_run.py'
    member.write_bytes(member.read_bytes() + bytes([10])
                      + b'# tampered after the receipt was written' + bytes([10]))

    with pytest.raises(bundle.BundleRefused, match='does not match the receipt'):
        bundle.verify_run_bundle(destination)

    # 摘要不改、只把 receipt 记的字节数改错：字节数同样是 §4 的判据。
    receipt_path = destination / 'bundle-receipt.json'
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    receipt['members']['scripts/rf_teaching_install_state.py']['bytes'] += 1
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding='utf-8')

    with pytest.raises(bundle.BundleRefused, match='does not match the receipt'):
        bundle.verify_run_bundle(destination)


def test_section_4_member_verification_precedes_every_board_action(tmp_path):
    """§4（复核先于 control.arm、不得写发布/系统路径、结果写 dispatch/<attempt_id>.json）
    + §6.1/§8.4（失败不消耗一次性授权）+ §7 `RUN_REFUSED_BUNDLE`。

    发布目录故意也不可核验：若实现先核验发布身份、再复核运行包，就会得到
    `RUN_REFUSED_IMPORT`；本用例据此判"运行包复核是第一个门"，并检查零硬件动作的
    可观察证据（账本未追加、无只读观察收据、发布目录逐字节未变）。
    """
    release = tmp_path / 'releases' / ('e' * 32)
    release.mkdir(parents=True)
    (release / 'merged_release_manifest.json').write_text('{}', encoding='utf-8')
    layout = _board_layout(tmp_path, release=release)
    tampered = layout['bundle'] / 'scripts' / 'rf_teaching_install_state.py'
    tampered.write_bytes(tampered.read_bytes() + bytes([10])
                        + b'# tampered after the receipt was written' + bytes([10]))
    before = _tree_digest(release)

    result = _run_entry(layout, release)
    assert result['exit'] != 0, (
        '期望：运行包复核失败时退出码非零（§7）；实际：exit=%r；stdout=%r；stderr=%r'
        % (result['exit'], result['stdout'][-1500:], result['stderr'][-1500:]))
    document = _outcome(result['stdout'])
    assert document is not None, (
        '期望：入口打印 "HEADROOM_RUN <json>"（§7 终态观察点）；实际：stdout=%r；stderr=%r'
        % (result['stdout'][-1500:], result['stderr'][-1500:]))
    assert document.get('terminal') == 'RUN_REFUSED_BUNDLE', (
        '期望：成员摘要不符 → §7 具名结局 RUN_REFUSED_BUNDLE，且先于发布身份/授权判定；'
        '实际：terminal=%r；reason=%r' % (document.get('terminal'), document.get('reason')))

    ledger_file = layout['state'] / 'ledger' / 'authorization.jsonl'
    assert not ledger_file.exists(), (
        '期望：复核失败时零硬件动作、零授权消费（§4 复核在任何硬件动作之前；§6.1 账本'
        '先于板卡接触）；实际：账本已被写入 %s' % ledger_file)
    marker = layout['state'] / 'dispatch' / (ATTEMPT_ID + '.json')
    assert marker.is_file(), (
        '期望：单次复核结果写入 §2/§4.3 的 dispatch/%s.json；实际：该文件不存在，'
        '状态根内容=%r' % (ATTEMPT_ID, sorted(str(p) for p in layout['state'].rglob('*'))))
    text = marker.read_text(encoding='utf-8')
    json.loads(text)
    assert 'rf_teaching_install_state.py' in text, (
        '期望：复核收据记录失败项（被改动的成员名，§4.3）；实际：%r' % text[:600])
    assert not (layout['state'] / 'observation' / (ATTEMPT_ID + '.json')).exists(), (
        '期望：零硬件动作时没有只读观察收据（§2/§8）；实际：observation/%s.json 存在'
        % ATTEMPT_ID)
    assert _tree_digest(release) == before, (
        '期望：复核实现不写任何发布/系统路径（§4）；实际：发布目录内容发生变化：%r -> %r'
        % (before, _tree_digest(release)))


# =============================================================== §5 启动参数
def test_section_5_parameters_are_fixed_board_side_without_bypass_switch():
    """§5：`--ledger-dir` 默认值是板端持久路径；证据根/观察目录分离且都在持久根下；
    §5 点名的放宽门开关（--force/--retry/--ignore-wiring-age/--skip-preflight）必须不存在。
    """
    run_parser, options, helps = _parser_surface()
    required = ('--session-id', '--authorization', '--ledger-dir', '--evidence-root',
                '--observation-dir', '--token-path', '--runner-version')
    missing = [flag for flag in required if flag not in options]
    assert not missing, (
        '期望：§5 固定参数 %r 全部存在；实际：缺少 %r；现有选项=%r'
        % (list(required), missing, sorted(options)))

    ledger_default = _parser_default(run_parser, 'ledger_dir', '--ledger-dir')
    assert ledger_default is not None and _norm(ledger_default) == _norm(LEDGER_DIR_DEFAULT), (
        '期望：--ledger-dir 默认值是板端持久路径 %r（§2/§5；不得再写"主机侧账本"）；'
        '实际：%r' % (LEDGER_DIR_DEFAULT, ledger_default))

    evidence_default = _parser_default(run_parser, 'evidence_root', '--evidence-root')
    observation_default = _parser_default(run_parser, 'observation_dir', '--observation-dir')
    for dest, value in (('--evidence-root', evidence_default),
                        ('--observation-dir', observation_default)):
        assert isinstance(value, str) and _norm(value).startswith(_norm(PERSISTENT_ROOT) + os.sep), (
            '期望：%s 的默认值位于板端持久根 %s 之下（§5 且不得指到教学记录目录）；'
            '实际：%r' % (dest, PERSISTENT_ROOT, value))
    assert _norm(evidence_default) != _norm(observation_default), (
        '期望：只读观察收据目录与证据根分离（§5/§2）；实际：两者都是 %r' % (evidence_default,))

    for flag in FORBIDDEN_SWITCHES:
        assert flag not in options, (
            '期望：§5 明确禁止任何放宽窗口/跳过门的开关，%s 不得存在；实际：解析器接受它'
            % flag)

    for option, help_text in sorted(helps.items()):
        if 'ledger' in option:
            assert 'host' not in (help_text or '').lower(), (
                '期望：§5 要求账本的文档与实现都必须是板端，不得再写"主机侧账本"；'
                '实际：%s 的帮助文本仍写主机侧：%r' % (option, help_text))


def test_section_5_the_rebuilt_bundle_binds_runner_version_1_0_3(tmp_path):
    """§5/§1.4：行为变更（HEADROOM 引脚迁移到新候选）后的运行包必须绑定 runner_version=1.0.3。

    判据：打包器产出的 receipt.runner_version、包内 runner 的 RUNNER_VERSION 常量
    都与 1.0.3 一致；把新 runner 标成别的版本（例如旧包 1.0.0）必须被拒绝——
    receipt 与包内常量绑定，不允许两者漂移。
    """
    bundle = _bundle_module()
    destination = tmp_path / 'run-bundle-101'
    receipt = bundle.build_run_bundle(REPO, destination, source_commit=SOURCE_COMMIT,
                                      builder=BUILDER)
    packaged = _runner_version_from_source(
        destination / 'scripts' / 'rf_headroom_research_run.py')
    assert packaged == '1.0.3', (
        '期望：HEADROOM 引脚迁移是行为变更 -> 包内 runner 版本 1.0.3（§5）；实际：%r' % packaged)
    assert receipt.get('runner_version') == '1.0.3', (
        '期望：receipt.runner_version == 1.0.3（§1.4/§5）；实际：%r'
        % receipt.get('runner_version'))

    with pytest.raises(bundle.BundleRefused, match='runner version'):
        bundle.build_run_bundle(REPO, tmp_path / 'run-bundle-stale',
                                source_commit=SOURCE_COMMIT, builder=BUILDER,
                                runner_version='1.0.0')


def test_section_5_a_runner_version_that_is_not_the_packaged_one_is_refused(tmp_path):
    """§5/§7：--runner-version 与包内版本不一致 -> 具名 RUN_REFUSED_BUNDLE（零硬件动作）。

    板端入口的版本校验读的是 bundle-receipt.json 绑定的版本；用一个旧版本号启动必须
    在复核阶段被拒，而不是先做任何板卡动作。
    """
    release = tmp_path / 'releases' / ('a' * 32)
    release.mkdir(parents=True)
    layout = _board_layout(tmp_path, release=release)
    receipt = json.loads((layout['bundle'] / 'bundle-receipt.json').read_text(encoding='utf-8'))
    assert receipt.get('runner_version') == '1.0.3', (
        '期望：本轮的运行包 receipt 绑定 1.0.3；实际：%r' % receipt.get('runner_version'))
    arguments = _entry_args(layout, release)
    arguments[arguments.index('--runner-version') + 1] = '1.0.0'
    entry = layout['bundle'] / 'scripts' / 'rf_headroom_research_run.py'
    proc = subprocess.run([sys.executable, '-B', str(entry)] + arguments,
                          cwd=str(layout['root']), env=_child_env(),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding='utf-8', errors='replace', timeout=180)
    document = _outcome(proc.stdout or '')
    assert document is not None, (
        '期望：版本不符时仍打印 "HEADROOM_RUN <json>"（§7 终态观察点）；'
        '实际：stdout=%r；stderr=%r' % ((proc.stdout or '')[-1500:], (proc.stderr or '')[-1500:]))
    assert document.get('terminal') == 'RUN_REFUSED_BUNDLE', (
        '期望：--runner-version 与包内 1.0.3 不符 -> §7 的 RUN_REFUSED_BUNDLE；'
        '实际：terminal=%r；reason=%r' % (document.get('terminal'), document.get('reason')))
    assert document.get('retry_allowed') is False
    assert proc.returncode != 0
    ledger_file = layout['state'] / 'ledger' / 'authorization.jsonl'
    assert not ledger_file.exists(), (
        '期望：版本不符时零授权消费（§6.1 账本先于板卡接触，但复核失败不消耗）；'
        '实际：账本已被写入 %s' % ledger_file)


# =============================================================== §6 单次派发与防重放
def test_section_6_the_dispatch_marker_is_created_with_o_excl_semantics(tmp_path):
    """§2/§6.2：`dispatch/<attempt_id>.json` 以 O_EXCL 创建；重复派发被拒。

    O_EXCL 的可观察语义 = 目标已存在时"创建"必须失败（`FileExistsError`），而不是
    "先看一眼再写"；被拒绝后既有内容必须原样保留（不得截断/覆盖）。
    """
    module = _runner()
    create_marker = _attr(module, 'create_dispatch_marker', '§2/§6.2', related='dispatch')
    dispatch_dir = tmp_path / 'dispatch'
    dispatch_dir.mkdir()
    document = dict(members=len(BUNDLE_MEMBERS), failures=[], conclusion='RUN_REFUSED_BUNDLE')

    path = Path(create_marker(dispatch_dir, ATTEMPT_ID, document))
    assert _norm(path) == _norm(dispatch_dir / (ATTEMPT_ID + '.json')), (
        '期望：派发标记落在 §2 的 dispatch/<attempt_id>.json；实际：%s' % path)
    assert json.loads(path.read_text(encoding='utf-8')) == document, (
        '期望：复核结果（成员数/失败项/结论，§4.3）写入标记；实际：%r'
        % path.read_text(encoding='utf-8'))
    before = path.read_bytes()

    with pytest.raises(FileExistsError):
        create_marker(dispatch_dir, ATTEMPT_ID,
                      dict(members=0, failures=['rf_headroom_research_run.py'],
                           conclusion='RUN_REFUSED_BUNDLE'))
    assert path.read_bytes() == before, (
        '期望：重复派发被拒后既有标记逐字节保留（§6.2 已存在即拒绝）；实际：内容变成 %r'
        % path.read_text(encoding='utf-8'))

    stale = dispatch_dir / (OTHER_ATTEMPT_ID + '.json')
    stale.write_text('', encoding='utf-8')
    with pytest.raises(FileExistsError):
        create_marker(dispatch_dir, OTHER_ATTEMPT_ID, document)

    with pytest.raises(ValueError, match='attempt_id'):
        create_marker(dispatch_dir, 'NOT-A-32-HEX-ATTEMPT-ID', document)


def test_section_6_a_replayed_nonce_is_refused_and_the_ledger_stays_append_only(tmp_path,
                                                                              monkeypatch):
    """§2/§6.1：nonce 追加进板端账本并 fsync；同 nonce 不同 attempt_id 一律拒绝。

    判据：账本文件是 `<ledger-dir>/authorization.jsonl`；`consume` 期间至少一次 fsync；
    记录落盘后可读且只含一条该 nonce 的记录；重放时 `refusal` 必须给出理由，且账本不得
    因重放而追加第二条（追加式账本的完整性）。
    """
    module = _runner()
    ledger_class = _attr(module, 'BoardAuthorizationLedger', '§2/§6.1', related='ledger')
    ledger_dir = tmp_path / 'ledger'
    ledger = ledger_class(ledger_dir)
    assert _norm(ledger.path) == _norm(ledger_dir / 'authorization.jsonl'), (
        '期望：板端账本文件是 §2 的 <ledger-dir>/authorization.jsonl；实际：%s' % ledger.path)
    assert ledger.refusal(NONCE) is None, (
        '期望：未使用的 nonce 不构成拒绝；实际：%r' % ledger.refusal(NONCE))

    syncs = []
    for name in ('fsync', 'fdatasync'):
        real = getattr(os, name, None)
        if real is None:
            continue

        def spy(fd, _real=real, _name=name):
            syncs.append((_name, fd))
            return _real(fd)

        monkeypatch.setattr(os, name, spy)

    ledger.consume(authorization_id=AUTHORIZATION_ID, nonce=NONCE, session_id=SESSION_ID,
                   attempt_id=ATTEMPT_ID)
    assert syncs, (
        '期望：账本每行追加后 fsync（§2/§6.1 先于板卡接触的持久记录）；'
        '实际：consume 期间没有调用 os.fsync/os.fdatasync')
    lines = [json.loads(line) for line in ledger.path.read_text(encoding='utf-8').splitlines()
             if line.strip()]
    assert len(lines) == 1 and lines[0].get('nonce') == NONCE, (
        '期望：账本恰好一条记录且带本次 nonce；实际：%r' % lines)

    replayed = ledger_class(ledger_dir)
    reason = replayed.refusal(NONCE)
    assert reason, ('期望：同一 nonce 在账本中已出现 → refusal 给出具名理由（§6.1/§6.2）'
                    '；实际：refusal 返回 %r' % reason)
    text_before = ledger.path.read_text(encoding='utf-8')
    try:
        replayed.consume(authorization_id=AUTHORIZATION_ID, nonce=NONCE,
                         session_id=SESSION_ID, attempt_id=OTHER_ATTEMPT_ID)
    except Exception as exc:                      # noqa: BLE001 - 具名拒绝或领域异常都算拒绝
        assert getattr(exc, 'label', None) in (None, module.RUN_REFUSED_AUTHORIZATION), (
            '期望：重放若抛异常，必须是 §7 的 RUN_REFUSED_AUTHORIZATION；实际：%r'
            % getattr(exc, 'label', type(exc).__name__))
    text_after = ledger.path.read_text(encoding='utf-8')
    appended = [json.loads(line) for line in text_after.splitlines() if line.strip()]
    same_nonce = [item for item in appended if item.get('nonce') == NONCE]
    assert len(same_nonce) == 1, (
        '期望：重放不得在账本里追加第二条同 nonce 记录（§6.1 已出现即拒绝）；'
        '实际：%d 条；账本前后：%r -> %r' % (len(same_nonce), text_before, text_after))


def test_section_6_the_ledger_survives_a_restart_and_a_torn_tail_is_repaired(tmp_path):
    """§6.3 + §2：重启（重新实例化）不等于新预算；撕裂尾行先截断再追加。

    "重启"在离线红态里的可判形式 = 新进程/新实例读同一持久账本：已消费的 nonce 仍被
    拒绝，记录数不因重新实例化而归零或翻倍；被杀死进程留下的撕裂尾行必须先截断，
    之后账本每一行都是合法 JSON。
    """
    module = _runner()
    ledger_class = _attr(module, 'BoardAuthorizationLedger', '§2/§6.3', related='ledger')
    ledger_dir = tmp_path / 'ledger'
    ledger_class(ledger_dir).consume(authorization_id=AUTHORIZATION_ID, nonce=NONCE,
                                    session_id=SESSION_ID, attempt_id=ATTEMPT_ID)

    restarted = ledger_class(ledger_dir)
    lines = [line for line in restarted.path.read_text(encoding='utf-8').splitlines()
             if line.strip()]
    assert len(lines) == 1, (
        '期望：账本在磁盘上（§2 /var/lib 持久），重新实例化后仍是那一条记录；实际：%r'
        % lines)
    assert restarted.refusal(NONCE), (
        '期望：板卡/进程重启后同一 nonce 仍被拒绝（§6.3 重启不等于新预算）；'
        '实际：refusal 返回 %r' % restarted.refusal(NONCE))

    with restarted.path.open('a', encoding='utf-8') as handle:
        handle.write('{"nonce": "torn-tail"')          # 模拟被杀进程留下的半行
    restarted.consume(authorization_id='ff' * 16, nonce=OTHER_NONCE, session_id=SESSION_ID,
                      attempt_id=OTHER_ATTEMPT_ID)
    repaired = [line for line in restarted.path.read_text(encoding='utf-8').splitlines()
                if line.strip()]
    try:
        parsed = [json.loads(line) for line in repaired]
    except ValueError as exc:
        raise AssertionError(
            '期望：撕裂尾行先被截断，账本每行都是合法 JSON（§2 沿用 attempt.jsonl 做法）；'
            '实际：解析失败 %s；账本=%r' % (exc, repaired))
    nonces = [item.get('nonce') for item in parsed]
    assert NONCE in nonces and OTHER_NONCE in nonces, (
        '期望：截断后新记录正常追加，旧 nonce 记录保留；实际：%r' % parsed)
    assert len([item for item in parsed if item.get('nonce') == NONCE]) == 1, (
        '期望：重新实例化与追加都不重复同 nonce 记录；实际：%r' % parsed)


# =============================================================== §7 结局枚举
def test_section_7_terminal_labels_are_stable_constants_not_scattered_literals():
    """§7 + §9.4：板端包新增的具名结局在包内是稳定常量，被同一集合收录，且不散落。

    判据：每个 §7 结局是模块级常量且值等于名字；全部进入既有终态集合
    `FIXED_TERMINAL_LABELS`（单一来源）；在运行包成员源码里，该字符串只能恰好出现
    一次，且只能作为同名常量的赋值出现。
    """
    module = _runner()
    wrong = [label for label in SECTION_7_LABELS if getattr(module, label, None) != label]
    assert not wrong, (
        '期望：§7 的具名结局都是值为自身的模块级常量；实际：缺失或不符 %r；'
        '现有 RUN_* 常量=%r'
        % (wrong, sorted(name for name in vars(module) if name.startswith('RUN_'))))

    labels = _attr(module, 'FIXED_TERMINAL_LABELS', '§7 稳定集合', related='LABEL')
    absent = sorted(set(SECTION_7_LABELS) - set(labels))
    assert not absent, (
        '期望：§7 的全部结局都收进 FIXED_TERMINAL_LABELS（避免散落字面量）；'
        '实际：不在集合里 %r；现有集合=%r' % (absent, sorted(labels)))

    for source_path in (RUNNER_FILE, CHECKER_FILE):
        tree = ast.parse(source_path.read_text(encoding='utf-8'))
        counts, assignments = {}, {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and type(node.value) is str:
                counts[node.value] = counts.get(node.value, 0) + 1
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value, ast.Constant)
                    and type(node.value.value) is str):
                assignments[node.targets[0].id] = node.value.value
        for label in SECTION_7_LABELS:
            if assignments.get(label) != label:
                raise AssertionError(
                    '期望：%s 在 %s 里以「NAME = 同名常量」的形式定义（§7 稳定常量）；'
                    '实际：%r' % (label, source_path.name, assignments.get(label)))
            assert counts.get(label, 0) == 1, (
                '期望：%s 只作为常量赋值字面量出现一次（§7 不是散落字面量）；'
                '实际：出现 %d 次' % (label, counts.get(label, 0)))
