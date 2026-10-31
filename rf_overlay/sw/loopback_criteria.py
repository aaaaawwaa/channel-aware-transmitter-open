#!/usr/bin/env python3
"""C6-C16: what the on-chip TX->RX loopback actually proves, and what it does not.

The loopback is the only way to get samples back without a cable, so it carries
the whole sample-path gate's data-integrity claims.  It is also unusually easy
to draw a wrong conclusion from.  The first pass at this concluded "I is
identically zero, the captured frequency is 2x the transmitted one, the
amplitude is 2032 against a buffer holding 1500".  None of that was about the
loopback: the TX source select was 0, so the buffer was never being played, and
the numbers described the DDS path with whatever the previous experiment had
left in it.  A later run of the same nominal procedure, with the source actually
pointed at the buffer, gives I = Q = 1500 exactly.

So the criteria below are built to be able to FAIL, and to say so:

  * every comparison is EXACT integer arithmetic.  There is no tolerance to
    sneak under, because the path is bit-exact and a tolerance would only hide
    the difference between "correct" and "nearly correct".
  * the I/Q mapping is SEARCHED over all eight signed swaps and the answer is
    reported, rather than assumed.  Reporting a search means an ambiguous
    result looks ambiguous instead of looking like a finding.
  * the delay is searched per capture, and the size of the solution set is
    reported.  A shift borrowed from a previous capture is the mistake already
    recorded in the project's own notes (the scope-alignment pitfall): free
    running sources plus an asynchronous start make the phase irreproducible,
    so it has to be re-derived every time.

  C6   presence and exactness   captured == played, bit for bit
  C10  format boundary          the 16-bit slot's high nibble is sign extension
  C11  I/Q mapping              which of the eight signed swaps is wired
  C12  delay alignment          the shift is searched and its residual reported
  C13  decimation               captured D == host floor(boxcar / D), per point
  C14  invalid input            lengths and ratios are rejected, not clamped
  C15  read consistency         done/pos before reading, and stillness after
  C16  loop boundary            the wrap inserts and drops nothing
  NV2  negative control         loopback closed -> the pattern must NOT come back

A second round of the same mistake happened after the first was fixed, and it
is the reason C13 and C16 now look the way they do.  Both were fitting models
that could not have matched:

  * C13 fitted a pure tone, where a phase error and a shift are the same
    observation and D-sample averaging attenuates the fit -- so the residual
    fell with D and read as a small unexplained error rather than as a
    criterion that cannot pass;
  * C16 fitted tx_sine(48, 1) against a buffer holding tx_sine(64, 1) read with
    a 48-word loop.  Those are different waveforms; the played one is the first
    48 points of the 64-point buffer.

Both now fit the asymmetric pattern and the actually-played buffer, and both
are exact at every D and at the wrap.  The general rule this keeps re-teaching:
a residual is evidence about the PATH only after the MODEL is shown to be
capable of fitting it exactly.

  --selftest  runs the analysis against synthetic captures and, just as
              importantly, against captures it must REJECT.
  --board --phy-mode 1r1t|2r2t
              runs the criteria on hardware.  `phy-mode` has no default that is
              safe to guess: on a 2R2T board a bare `init` resets the AD9361 and
              then aborts, leaving the part unprogrammed.  Read the mode off the
              board (`plcfg` reports it) and pass it.
"""

import math
import os
import re
import subprocess
import sys

AMP = 1500
FS = 60000000
N = 64                       # capture length, in decimated samples
SSH = os.environ.get(
    "PYNQ_SSH",
    "<LOCAL_PATH>")
APP = "/home/xilinx/board_app_src/board_app"

# `init`'s own default is atten 0 -- full TX output -- and TX1A is cabled to
# RX1A.  Kept equal to scope_tx_check.py's SAFE_ATTEN_MDB; duplicated rather
# than imported because that module pulls in pyvisa and this one is stdlib-only.
SAFE_ATTEN_MDB = 40000

# The IDELAY load `plcfg` performs, duplicated rather than shared because the
# two paths must be able to diverge: `plcfg` writes these and `phy_mode = 1` in
# the same breath (rf_main.c:669-671), so a 2R2T run -- which must NOT call
# plcfg -- has no way to get the values except to carry them itself.
IDELAY_EN = 0x7F
IDELAY_TAP = 15

# --------------------------------------------------------------------------
# The host model.  This is the independent side of every comparison: if it were
# derived from the capture, matching it would prove nothing.
# --------------------------------------------------------------------------

# The eight signed swaps of (i, q).  A two-vector has four signed permutations:
# pick which component lands in the I slot (2 ways), then the sign of each (4
# ways) -- eight in total, and all eight are physically reachable by crossing
# or inverting a differential pair.
MAPPINGS = {}
for _first in ("i", "q"):
    for _s1 in (1, -1):
        for _s2 in (1, -1):
            _name = "%s%s,%s%s" % ("+" if _s1 > 0 else "-", _first,
                                   "+" if _s2 > 0 else "-",
                                   "q" if _first == "i" else "i")
            MAPPINGS[_name] = (_first, _s1, _s2)


def apply_mapping(m, i, q):
    first, s1, s2 = m
    return (s1 * i, s2 * q) if first == "i" else (s1 * q, s2 * i)


def tx_sine(n, k, amp=AMP, real=False):
    """The buffer that `board_app samples iq|real n k` fills.

    int() truncates toward zero, which is what C's (int) cast does and what the
    firmware therefore wrote into the BRAM.  round() would be a different buffer
    and the comparison would be against something nobody is playing.
    """
    out = []
    for j in range(n):
        ph = 2.0 * math.pi * k * j / n
        ii = int(amp * math.cos(ph))
        qq = 0 if real else int(amp * math.sin(ph))
        out.append((ii, qq))
    return out


def stream(tx, ratio, shift, n_out):
    """What the RX buffer should hold: the TX pattern streamed from sample
    `shift`, boxcar averaged in non-overlapping groups of D = 2**ratio.

    The divide is an arithmetic right shift in the RTL, so it FLOORS negatives
    rather than rounding to nearest.  Python's >> on a negative int does the
    same, which is why this is written as a shift and not a // by D -- those
    agree, but writing the shift makes the match with the RTL obvious.
    """
    L = len(tx)
    D = 1 << ratio
    out = []
    for m in range(n_out):
        ai = aq = 0
        for k in range(D):
            idx = (shift + m * D + k) % L
            ai += tx[idx][0]
            aq += tx[idx][1]
        out.append((ai >> ratio, aq >> ratio))
    return out


def solve(captured, tx, ratio):
    """Search every (mapping, shift).  Returns (exact, best).

    `exact` is every pair that reproduces the capture with zero error -- a set,
    not a winner, because when more than one pair fits the capture does not
    determine the answer and reporting a single element would manufacture a
    finding out of an ambiguity.

    `best` is the minimum error achieved by any pair, which is what says
    whether the data is off by a bit or off by a mapping.
    """
    L = len(tx)
    bases = {s: stream(tx, ratio, s, len(captured)) for s in range(L)}
    exact, best = [], None
    for name, m in MAPPINGS.items():
        for s in range(L):
            err = 0
            for (bi, bq), (ci, cq) in zip(bases[s], captured):
                mi, mq = apply_mapping(m, bi, bq)
                e = max(abs(mi - ci), abs(mq - cq))
                if e > err:
                    err = e
            if best is None or err < best[0]:
                best = (err, name, s)
            if err == 0:
                exact.append((name, s))
    return exact, best


def sign_extended(words):
    """C10: is the unused high nibble of each 16-bit slot a sign extension?

    Returns (n_checked, n_consistent).  The slot holds a 12-bit quantity, so
    bits 15:12 of the I half must equal bit 11, and bits 31:28 of the Q half
    must equal bit 27.  Software that masks to 12 bits and extends itself does
    not care -- but software that reads the slot as a signed 16-bit value DOES,
    and the two answers differ by 2048 for every negative sample.  Ours masks,
    so this is about knowing which contract the PL is offering.
    """
    n_ok = 0
    for w in words:
        if ((w >> 15) & 1) == ((w >> 11) & 1) and \
           ((w >> 31) & 1) == ((w >> 27) & 1):
            n_ok += 1
    return len(words), n_ok


def iq(w):
    i = w & 0xFFF
    q = (w >> 16) & 0xFFF
    return (i - 0x1000 if i & 0x800 else i,
            q - 0x1000 if q & 0x800 else q)


def parse_raw(text):
    return [int(m, 16) for m in re.findall(r"^0x([0-9A-Fa-f]{8})$", text, re.M)]


# --------------------------------------------------------------------------
# board driver
# --------------------------------------------------------------------------

class Board(object):
    def __init__(self, dry=False):
        self.dry = dry
        self.log = []

    def run(self, *args):
        """One board_app invocation.  Each is its own process on the board, so
        nothing is shared between them except the hardware -- which is exactly
        the property that made the earlier 'I is zero' reading possible, since
        the source select lived on the other side of a process boundary."""
        cmd = "echo $PYNQ_PASSWORD | sudo -S %s %s" % (APP, " ".join(str(a) for a in args))
        return self._ssh(cmd)

    def _ssh(self, cmd, timeout=300):
        if self.dry:
            self.log.append(cmd)
            return ""
        out = subprocess.run(
            [sys.executable, SSH, "run", cmd],
            capture_output=True, text=True, timeout=timeout)
        if out.returncode != 0:
            raise RuntimeError("ssh failed: %s\n%s" % (cmd, out.stderr))
        return out.stdout

    def raw_words(self, n=N):
        out = self._ssh(
            "echo $PYNQ_PASSWORD | sudo -S env PYTHONPATH=/home/xilinx XILINX_XRT=/usr "
            "python3 /home/xilinx/rx_raw.py %d 0 --raw" % n)
        return parse_raw(out)


def capture(b, pattern, ratio=0, n=N, smp_len=None, loopback=True):
    """Write a pattern, arm one capture, wait for it, read it back.

    `pattern` is ("samples", (mode, points, k)) or ("words", [(i, q), ...]).
    `smp_len` is the reader's loop length, defaulting to the capture length N;
    C16 sets it SHORTER than the pattern on purpose, so that the reader has to
    wrap mid-pattern and any sample inserted or dropped at the wrap shows up as
    a mismatch.

    The buffer is zeroed before every pattern.  Not hygiene for its own sake:
    the first version of this wrote only the eight asymmetric words and left
    8..63 holding whatever the previous case had put there, which made the
    expected buffer depend on the history of the run rather than on anything
    this function set.  That is the same class of mistake as assuming the TX
    source select is still where the last command put it.

    The ordering below is the other part that was wrong before, and it is why
    the earlier "I is identically zero" reading was reachable at all:

      * buffer contents are plain AXI writes, so they can go anytime;
      * `smpcfg`'s commit crosses into the DATA_CLK domain and is latched by a
        toggle there, so it must come AFTER init has given that clock a source
        -- a commit issued while DATA_CLK is dead is never seen at all;
      * `init` resets the part and clears the loopback bit, so the loopback has
        to be re-armed after it, every time;
      * the source select is an AXI register that survives everything except a
        PL reprogram, but its loss is INVISIBLE in the capture -- a wrong
        source still yields plausible samples -- so it is re-asserted and read
        back on every capture rather than assumed.

    `loopback=False` closes the tap instead of opening it, which is the negative
    control the whole criterion rests on: "the pattern came back" only says
    something if a closed loopback would have shown it not coming back.  It has
    to be a parameter rather than a call made afterwards, because the arming
    above is the last thing that touches that bit.
    """
    kind, args = pattern
    b.run("samples", "zeros", N)
    if kind == "words":
        for w, (i, q) in enumerate(args):
            b.run("samples", "write", w, (q & 0xFFF) << 16 | (i & 0xFFF))
    else:
        b.run("samples", args[0], args[1], args[2])
    b.run("smpcfg", N if smp_len is None else smp_len, 1)

    b.run("srcsel", 1)
    sel = b.run("smpstat", 1)
    if "src_sel = 1" not in sel:
        raise RuntimeError("source select did not take:\n" + sel)

    # regwrite <addr> <value> [mask], and the mask is the THIRD argument.
    # The first version of the negative control passed (value 1, mask 0), which
    # this command correctly reads as "write nothing" -- so it left the loopback
    # ARMED and then reported the pattern coming back, which is exactly what a
    # working loopback reports.  A control that cannot fail is worse than no
    # control, because its output reads as evidence.  The readback check below
    # is what makes that impossible to repeat: the bit is verified, not assumed.
    rb = b.run("regwrite", "0x3F5", 1 if loopback else 0, 1)
    m = re.search(r"readback 0x([0-9A-Fa-f]{2})", rb)
    if not m or (int(m.group(1), 16) & 1) != (1 if loopback else 0):
        raise RuntimeError("loopback bit did not take:\n" + rb)

    b.run("capcfg", ratio, n, 0)
    b.run("caparm", 0)
    b.run("caparm", 1)

    # done is what says the buffer is finished.  Reading before it is set means
    # reading a buffer that is still being overwritten, which yields a mixture
    # of two captures that looks entirely plausible.
    for _ in range(200):
        st = b.run("capstat", 1)
        m = re.search(r"cap_pos = (\d+)\s+done = (\d+)", st)
        if m and m.group(2) == "1":
            break
    else:
        raise RuntimeError("capture never reported done:\n" + st)
    return b.raw_words(n)


# --------------------------------------------------------------------------
# the one place `init` is built
# --------------------------------------------------------------------------

def init_args(atten_mdb=SAFE_ATTEN_MDB, lo_hz=80000000, want_2r2t=False):
    """The argv for `init`, with whichever phy-dependent knobs are wanted.

    Two arguments are not optional in practice:

    * `atten` DEFAULTS TO THE SAFE FLOOR here, not to `init`'s own 0.  TX1A is
      cabled to RX1A, so 0 mdB means the full TX output drives the receiver
      input.  Making the safe value this function's default is what makes
      "someone forgot `atten`" impossible rather than merely unlikely.

    * `2r2t` MUST travel with the mode, and getting it wrong is destructive
      rather than merely wrong.  `init` drives RESETB low then high and only
      THEN reads phy_mode back (the gpio block in rf_main.c's `init`, before
      ad9361_init), so a bare `init` issued while the PL sits in 2R2T resets
      the AD9361 and then aborts on the assertion -- leaving the part
      unprogrammed mid-run, with the abort text pointing at the PL rather than
      at the caller.  Passing 2r2t makes init write phy_mode = 0 itself and
      expect 0, which is the supported way to move both sides together.

    This is the same convention as scope_tx_check.board_init, which has an AST
    selftest enforcing it; bare_init_calls below is that guard for this module,
    where there is no board_init to name.
    """
    args = ["init", "atten", "%d" % int(atten_mdb), "lo", "%d" % int(lo_hz)]
    if want_2r2t:
        args.append("2r2t")
    return args


def idelay_args(tap=IDELAY_TAP):
    """The argv that loads the RX pin deserialiser's input delay.

    This has to be issued explicitly rather than inherited from a `plcfg` run
    earlier in the session, and the reason is the same one that makes the
    source select be re-asserted on every capture (see `capture`): a wrong or
    absent IDELAY load is INVISIBLE in the data.  A capture taken with the
    primitives at their power-on tap is not flagged, not empty and not
    obviously wrong -- it is a plausible capture with a few samples off.  So
    the value is set by the run that depends on it, every time.

    Measured A/B on this board, same bitstream, minutes apart, with everything
    else held: `idelay_en = 0x7F` is bit-exact on 1R1T, and `idelay_en = 0`
    leaves 9 of 64 samples off with a worst error of 46.  On 2R2T with no load
    there is no exact solution at all and the per-criterion best mappings
    disagree with each other -- the signature of nothing aligning.

    0x7F covers all seven primitives: six data lanes plus the frame line
    (phy_rx.v:89/143).  With a constant 1 there `LD` is held asserted, so the
    primitives track `idelay_tap` continuously and a tap sweep needs no
    stop/start.
    """
    return ["idelay", "0x%02X" % IDELAY_EN, "%d" % int(tap)]


def bare_init_calls(path):
    """Every `init` issued without going through init_args, as (line, text).

    A literal-only scan with ast: the thing being prevented is a future call
    site typing `b.run("init", ...)` by hand, which is invisible to every test
    that exercises this module's ANALYSIS.  Only the module's own call sites are
    judged; the argv builder above is where "init" is allowed to appear.
    """
    import ast
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and f.attr == "run"):
            continue
        if not node.args:
            continue
        a = node.args[0]
        if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                and a.value.split()[:1] == ["init"]:
            out.append((node.lineno, a.value.split()[0]))
    return out


def idelay_calls(path):
    """(sites inside `board_run` that name `idelay_args`, hand-typed idelay runs).

    Same shape of guard as bare_init_calls, for the same reason: a dropped
    IDELAY load is not a crash.  Every criterion below would still run and still
    print numbers; they would just be numbers taken at an unloaded tap, which
    this module has already been fooled by once.  So the board path is required
    to name the argv builder rather than repeat its values, and the values
    themselves are pinned by the first check in `selftest`.

    Hand-typed `b.run("idelay", ...)` is reported separately from a missing
    call: the two failures read the same in the output but have different
    causes, and re-introducing the raw form is how en/tap would drift away from
    what the other modes use.
    """
    import ast
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())

    named = []
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name == "board_run":
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) \
                        and isinstance(node.func, ast.Name) \
                        and node.func.id == "idelay_args":
                    named.append(node.lineno)

    hand = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run" and node.args):
            continue
        a = node.args[0]
        if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                and a.value.split()[:1] == ["idelay"]:
            hand.append((node.lineno, a.value.split()[0]))
    return named, hand


# --------------------------------------------------------------------------
# criteria
# --------------------------------------------------------------------------

def selftest():
    """Prove the analysis before it is pointed at hardware.

    A search that always returns the same answer whatever it is given would
    pass every criterion above while measuring nothing.  So each case here
    builds the capture from the model with a known answer, and checks both that
    the analyser finds it and that a plausible wrong answer is rejected.
    """
    fails = 0

    def chk(name, cond):
        nonlocal fails
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            fails += 1

    tx = tx_sine(64, 1)
    asym = [(1000, 100), (-500, 700), (300, -900), (-1200, -400),
            (650, 250), (-150, 1100), (900, -50), (-800, 850)]
    pad = asym + [(0, 0)] * (64 - len(asym))

    # The truth as measured on the board: the loopback crosses I and Q.
    for truth in ("+q,+i", "+q,-i", "+i,+q", "-q,+i"):
        cap = [apply_mapping(MAPPINGS[truth], i, q)
               for i, q in stream(pad, 0, 17, N)]
        exact, _ = solve(cap, pad, 0)
        chk("recovers %s from an asymmetric capture built with it" % truth,
            truth in set(nm for nm, _ in exact))

    # A one-sample shift error must be caught: if the search cannot tell 17 from
    # 18 then the alignment criterion is vacuous.
    cap = [apply_mapping(MAPPINGS["+q,+i"], i, q)
           for i, q in stream(pad, 0, 17, N)]
    exact, _ = solve(cap, pad, 0)
    chk("the asymmetric pattern pins one mapping and one shift (got %r)"
        % (exact,), exact == [("+q,+i", 17)])

    # A capture that is simply wrong must not come back with error 0.  This is
    # the guard against a comparison that is true by construction.
    cap = [(c[0] + 3, c[1]) for c in
           [apply_mapping(MAPPINGS["+q,+i"], i, q) for i, q in stream(pad, 0, 5, N)]]
    _, best = solve(cap, pad, 0)
    chk("rejects a capture that is off by 3 LSB (best error %d)" % best[0],
        best[0] == 3)

    # Decimation has to be modelled with the RTL's flooring, not round-to-
    # nearest.  An odd group sum is where the two rules disagree, and the
    # negative one separates flooring from truncation-toward-zero as well:
    # +3/2 floors to 1, -3/2 floors to -2 (truncation would say -1, and
    # rounding to nearest would say 2 and -1 respectively).
    odd = [(3, 3), (0, 0), (-3, -3), (0, 0)]
    chk("decimation model floors rather than rounds or truncates",
        stream(odd, 1, 0, 2) == [(1, 1), (-2, -2)])

    # The k=1 sine is rotationally symmetric, so the analyser must be EXPECTED
    # to report more than one solution rather than pretending to a precision the
    # data does not have.  FOUR solutions fit, and they are all one physical
    # mapping seen through the symmetry, because a rotation of a pure sine is
    # indistinguishable from a shift of it:
    #
    #   -i,-q  is a 180 deg rotation  -> the same capture 32 samples along
    #   +q,-i  is a  90 deg rotation  -> 16 samples along
    #   -q,+i  is a -90 deg rotation  -> 48 samples along
    #
    # This is not a defect in the search, it is the reason the criteria below
    # use the asymmetric pattern: on a sine, "which I/Q mapping is wired" and
    # "where did the capture start" cannot be separated at all.  Getting this
    # out of the way first is what makes the asymmetric result mean something.
    cap = [apply_mapping(MAPPINGS["+i,+q"], i, q) for i, q in stream(tx, 0, 9, N)]
    exact, _ = solve(cap, tx, 0)
    chk("k=1 sine: exactly the four rotation-or-shift solutions fit (got %r)"
        % (sorted(exact),),
        set(exact) == {("+i,+q", 9), ("-i,-q", 41),
                       ("+q,-i", 25), ("-q,+i", 57)})

    # `init` on a 2R2T board without the keyword drives RESETB low/high and only
    # THEN reads phy_mode back, so it resets the AD9361 and aborts -- leaving the
    # part unprogrammed in the middle of a measurement, with the abort text
    # pointing at the PL rather than at this call.  That trap is why the mode
    # travels with the init here, and the checks below are what keeps a future
    # call site from re-introducing a bare one.
    chk("the 2r2t init carries the keyword that makes init write phy_mode",
        "2r2t" in init_args(SAFE_ATTEN_MDB, 80000000, True))
    chk("...and a 1R1T init does not carry it",
        "2r2t" not in init_args(SAFE_ATTEN_MDB, 80000000, False))
    chk("the init carries the SAFE attenuation, not init's own default of 0",
        init_args(SAFE_ATTEN_MDB, 80000000, False)[:3]
        == ["init", "atten", "%d" % SAFE_ATTEN_MDB])

    bare = bare_init_calls(__file__)
    chk("no call site issues `init` without going through init_args (found %r)"
        % (bare,), bare == [])

    # IDELAY is loaded by name, and its values are pinned here rather than
    # wherever the board path happens to be.  This is the guard with no runtime
    # symptom: a run with no IDELAY load still captures, still compares and
    # still prints numbers -- they are simply numbers taken at whatever tap the
    # primitives powered up on.
    chk("idelay_args pins the en/tap `plcfg` writes (%r)" % (idelay_args(),),
        idelay_args() == ["idelay", "0x7F", "%d" % IDELAY_TAP]
        and IDELAY_EN == 0x7F and IDELAY_TAP == 15)
    named, hand = idelay_calls(__file__)
    chk("board_run loads IDELAY through idelay_args (%d site(s) at %r)"
        % (len(named), named), len(named) >= 1)
    chk("no call site hand-types `b.run(\"idelay\", ...)` (found %r)"
        % (hand,), hand == [])

    print("=== %d FAIL ===" % fails)
    return 1 if fails else 0


def board_run(want_2r2t=False):
    b = Board()
    results = {}

    # init once.  It is slow (~40 s) and it is the only thing here that needs the
    # part to be reconfigured at all.
    #
    # `atten` is NOT the default 0.  TX1A is cabled to RX1A, so 0 mdB means the
    # full TX output drives the receiver input, and this script armed that on
    # every run until the cable existed to make it matter.  SAFE_ATTEN_MDB is
    # the same 40 dB the scope checks use.  C8 measured the digital-loopback
    # capture to be point-for-point identical at 0 / 10000 / 30000 mdB -- the
    # tap is upstream of the attenuator -- so the criteria below do not care
    # which value is here, and the safe one costs nothing.
    #
    # The reply is PRINTED rather than discarded.  `init` is the only step whose
    # failure leaves every criterion below measuring a part that was never
    # programmed, and the reply is where that shows: its criterion-2.5 lines
    # carry the sampling rate read back out of the part's own clock-tree
    # registers, and an abort carries the assertion text instead.
    print("--- init%s" % (" 2r2t" if want_2r2t else ""))
    init_reply = b.run(*init_args(SAFE_ATTEN_MDB, 80000000, want_2r2t))
    print(init_reply)
    results["init reply"] = " ".join(init_reply.split())[:200]

    # Load IDELAY before anything is captured, and do not take the write on
    # trust.  On 1R1T this reproduces what `plcfg` would have left behind, so
    # the criteria below are unchanged; on 2R2T it is the ONLY way the path is
    # loaded at all, since `init` never writes IDELAY and `plcfg` cannot be run
    # there.  `init` is a no-op for this register either way -- it writes
    # phase_inc and phy_mode only -- so loading after it is not a race.
    print("--- idelay %s" % idelay_args())
    idelay_reply = b.run(*idelay_args())
    print(idelay_reply)
    results["idelay reply"] = " ".join(idelay_reply.split())[:200]
    if "RESULT PASS" not in idelay_reply:
        raise RuntimeError("IDELAY did not load -- every sample below would "
                           "be sampled at an unloaded tap and look plausible "
                           "anyway:\n" + idelay_reply)

    b.run("samples", "iq", 64, 1)
    b.run("smpcfg", 64, 1)
    b.run("srcsel", 1)
    print("--- smpstat (source must be advancing before anything is captured)")
    print(b.run("smpstat", 4))

    asym = [(1000, 100), (-500, 700), (300, -900), (-1200, -400),
            (650, 250), (-150, 1100), (900, -50), (-800, 850)]

    # ---- C10 / C11 / C12: one asymmetric pattern, exact match --------------
    print("--- C10/C11/C12: asymmetric pattern")
    w = capture(b, ("words", asym))
    nw, ok = sign_extended(w)
    results["C10 sign extension"] = (nw, ok)
    cap = [iq(x) for x in w]
    pad = asym + [(0, 0)] * (64 - len(asym))
    exact, best = solve(cap, pad, 0)
    results["C11/C12 mapping+shift"] = (exact, best)
    print("  C10 sign-extended slots: %d/%d" % (ok, nw))
    print("  C11/C12 exact solutions: %r   best near-miss: %r" % (exact, best))

    # ---- C13: decimation, per point, against the host model ----------------
    #
    # The pattern is the ASYMMETRIC one, not a tone, and that is the difference
    # between a criterion that can fail for the right reason and one that cannot
    # fail at all.  On a tone the search absorbs a phase error as a shift, and
    # averaging D samples attenuates a tone by about sinc(pi f D / fs) -- so the
    # residual falls with D whether or not the decimator is right.  Measured
    # that way this sweep read 31/9/8/2 and was written down as an unexplained
    # quantisation-scale residual.  On the asymmetric pattern, which the
    # selftest pins to exactly one (mapping, shift), the same sweep is 0 at
    # every D: the decimator is exact, and the old numbers were the model's.
    print("--- C13: D sweep, asymmetric pattern")
    for ratio in (0, 1, 2, 3, 4):
        w = capture(b, ("words", asym), ratio=ratio)
        cap = [iq(x) for x in w]
        exact, best = solve(cap, pad, ratio)
        results["C13 D=%d" % (1 << ratio)] = (best[0], len(exact))
        print("  D=%-2d best error %d, %d exact solution(s) %r"
              % (1 << ratio, best[0], len(exact), best[1:]))

    # ---- C16: a short loop must wrap without inserting or dropping ---------
    #
    # The model is the first 48 points OF THE 64-POINT BUFFER, because that is
    # what a 48-word loop plays.  Comparing against tx_sine(48, 1) -- one whole
    # cycle in 48 points -- fits a different waveform entirely and reads 1060,
    # which is a statement about the model and not about the wrap.
    print("--- C16: reader loop 48 against a 64-point pattern")
    w = capture(b, ("samples", ("iq", 64, 1)), smp_len=48)
    tx48 = tx_sine(64, 1)[:48]
    cap = [iq(x) for x in w]
    exact, best = solve(cap, tx48, 0)
    results["C16 wrap len=48"] = (best[0], len(exact))
    print("  best error %d, %d exact solution(s) %r"
          % (best[0], len(exact), best[1:]))

    # ---- the negative control the exactness claims rest on ----------------
    print("--- NV2: loopback closed, same buffer, must NOT match")
    w = capture(b, ("words", asym), loopback=False)
    cap = [iq(x) for x in w]
    exact, best = solve(cap, pad, 0)
    results["NV2 loopback off"] = (best[0], len(exact))
    print("  best error %d, %d exact solution(s)  (must not be 0 / not 1)"
          % (best[0], len(exact)))

    # ---- C14: invalid input must be refused, not clamped -------------------
    print("--- C14: invalid input")
    cases = [("samples", "iq", 0, 1), ("samples", "iq", 5000, 1),
             ("smpcfg", 0, 1), ("smpcfg", 5000, 1),
             ("capcfg", 5, 64, 0), ("capcfg", 0, 0, 0), ("capcfg", 0, 5000, 0)]
    c14 = []
    for c in cases:
        out = b._ssh("echo $PYNQ_PASSWORD | sudo -S %s %s; echo RC=$?" %
                     (APP, " ".join(str(x) for x in c)))
        rc = re.search(r"RC=(\d+)", out)
        c14.append((c, rc.group(1) if rc else "?"))
        print("  %-28s -> rc=%s" % (" ".join(str(x) for x in c),
                                    rc.group(1) if rc else "?"))
    results["C14 rejected"] = c14

    # ---- C15: the done/pos handshake -------------------------------------
    print("--- C15: done/pos handshake, then stillness")
    b.run("samples", "iq", 64, 1)
    b.run("smpcfg", 64, 1)
    b.run("srcsel", 1)
    b.run("capcfg", 0, 64, 0)
    b.run("caparm", 0)
    b.run("caparm", 1)
    print(b.run("capstat", 6))
    a = b.raw_words()
    z = b.raw_words()
    results["C15 stable"] = (a == z)
    print("  two reads after done identical: %s" % (a == z))

    print()
    print("=== summary ===")
    for k, v in results.items():
        print("  %-24s %s" % (k, v))
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    if "--board" in sys.argv:
        # --phy-mode follows scope_tx_check.py's convention, default included:
        # the board's own PL state decides which mode is safe, and a run that
        # guessed would either abort the init (2R2T board, bare init) or measure
        # the wrong profile.  `plcfg` on the board reports phy_mode.
        mode = "1r1t"
        if "--phy-mode" in sys.argv:
            mode = sys.argv[sys.argv.index("--phy-mode") + 1]
            if mode not in ("1r1t", "2r2t"):
                print("--phy-mode must be 1r1t or 2r2t, not %r" % mode)
                raise SystemExit(2)
        raise SystemExit(board_run(want_2r2t=(mode == "2r2t")))
    print(__doc__)
    raise SystemExit(2)
