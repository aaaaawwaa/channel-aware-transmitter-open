# S2有上限单变量稳定等待对照 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 superpowers:subagent-driven-development 或 superpowers:executing-plans 按任务逐条实施。步骤用 `- [ ]` 勾选跟踪。

**Goal:** 在物理回环捕获中增加一个有上限、显式请求、可被时序证明的稳定等待，使"TX开启→RX武装"间隔成为可测的单变量，为削顶定位取得 0µs 与 1000µs 两组对照证据。

**Architecture:** 等待放在 daemon 现有唯一物理捕获交易内部（TX源开启返回之后、`rf_core_prepare_rx` 之前），因为客户端无法在请求中途介入。睡眠函数与 `now_ns` 一样**注入**，主机测试断言的是"确实请求了 N 微秒"而非"源码里有个 sleep"。等待不是默认行为：缺省请求与今天逐位一致。

**Tech Stack:** C（rf_daemon.c，主机 MinGW gcc + fake PL）；Python 3.8（rf_status_client.py / pytest）；既有 ndjson 协议。

**Spec:** `docs/superpowers/specs/2026-10-07-s2-settle-wait-design.md`

## Global Constraints

- 本轮**全部离线**：不连板、不安装、不采集、不 `Overlay()`、不布防、不发射。native ARM 构建、安装与实采属于上板阶段，**本计划一律 NOT_RUN**。
- 既有 dirty tree 原样保留，**不提交、不重置、不删除**任何已有工作。
- 不改 bit/HWH/core/vendor/CNN/BPSK/环境基线/诊断阈值；不新增 MMIO；不改 MGC 或增益档位。
- 等待上限 `RF_SETTLE_WAIT_MAX_US = 5000`；实验档位 `0` 与 `1000`。
- `settle_wait_us > 0` **必须**同时 `observe_timing: true`，否则拒绝。
- 缺省或 `0` ⇒ **零次睡眠、零次额外时钟读取**，交易与既有帧逐位不变。
- 协议 `RF_PROTOCOL_MINOR` 由 `4` 升到 `5`（纯增量，major 不变）。
- 每个任务独立 RED→GREEN，日志落在 `evidence/rf-teaching/s2/settle-wait-2026-10-07/`。

---

### Task 1: C daemon — 有上限可证明的稳定等待

**Files:**
- Modify: `rf_overlay/sw/board_app/rf_version.h`（minor 4→5）
- Modify: `rf_overlay/sw/board_app/rf_daemon.h`（注入 `sleep_us`）
- Modify: `rf_overlay/sw/board_app/rf_daemon.c`（参数、等待、trace 字段、dispatch 门禁）
- Test: `rf_overlay/sw/board_app/test/test_rf_daemon.c`

**Interfaces:**
- Consumes: 既有 `opt_uint` / `opt_bool` / `refuse` / `arg_error` / `capture_timing_valid` / `timing_clock_calls` / `fake_ctrl_writes`。
- Produces:
  - `void (*rf_daemon_config_t::sleep_us)(uint64_t us)`
  - `loopback.physical.capture` 新可选参数 `settle_wait_us`（int，0..5000）
  - `last_capture_timing` 新字段 `settle_wait_requested_us`，以及条件字段 `settle_begin_ns` / `settle_end_ns`
  - 常量 `RF_SETTLE_WAIT_MAX_US`
  - 主机测试夹具 `test_sleep_us` / `sleep_calls` / `sleep_total_us` / `sleep_at_ctrl_writes`

- [ ] **Step 1: 写失败测试**

在 `test/test_rf_daemon.c` 的 `test_now_ns` 下方加入替身与计数器：

```c
/* The injected settling wait.  It does NOT sleep: the host test asserts that the
 * requested duration actually reached an injected call, which is the thing that
 * can be wrong, rather than spending wall-clock to re-measure nanosleep. */
static unsigned sleep_calls;
static uint64_t sleep_total_us;
static int sleep_at_ctrl_writes;
static uint64_t last_sleep_us;

static void test_sleep_us(uint64_t us)
{
    ++sleep_calls;
    last_sleep_us = us;
    sleep_total_us += us;
    sleep_at_ctrl_writes = fake_ctrl_writes;
}
```

在 `fill_identity` 里接上（所有测试 daemon 都拿到它；未请求等待的路径不会调用它）：

```c
    cfg->now_ns = test_now_ns;
    cfg->sleep_us = test_sleep_us;
```

新增测试函数（放在 `test_capture_timing` 之后）：

```c
static void test_settle_wait(void)
{
    rf_core_t *core; rf_daemon_t *d; script_t s, base_frame;
    unsigned base_clocks; int j;
    printf("=== bounded opt-in settling wait ===\n");
    for (j = 0; j < 2; ++j) {
        core = healthy_core(); d = new_daemon(core);
        script_init(&s); timing_load(&s);
        timing_capture(&s, 3, j == 0 ? ",\"observe_timing\":true"
                                     : ",\"observe_timing\":true,\"settle_wait_us\":0");
        timing_clock_calls = 0; sleep_calls = 0; sleep_total_us = 0;
        check("zero-wait capture stays usable", serve(d, &s) == 0);
        if (j == 0) { base_clocks = timing_clock_calls; base_frame = s; }
        check("absent or zero settle_wait_us performs no sleep", sleep_calls == 0 && sleep_total_us == 0);
        check("absent or zero settle_wait_us adds no clock read", timing_clock_calls == base_clocks);
        check("zero wait request is reported as zero", timing_number(&s, "settle_wait_requested_us") == 0);
        check("zero wait publishes no settle bracket", timing_find(&s, "settle_begin_ns") == NULL);
        if (j == 1) {
            const char *a = timing_find(&s, "{\"id\":3,"); const char *b = timing_find(&base_frame, "{\"id\":3,");
            const char *end = a ? strchr(a, '\n') : NULL;
            check("explicit zero is byte-identical to absent", a && b && end &&
                  memcmp(a, b, (size_t)(end - a) + 1u + 16u) == 0);
        }
        rf_daemon_destroy(d); rf_core_destroy(core);
    }
    for (j = 0; j < 2; ++j) {
        long requested = j == 0 ? 1000 : RF_SETTLE_WAIT_MAX_US;
        int writes_before;
        core = healthy_core(); d = new_daemon(core);
        script_init(&s); timing_load(&s); serve(d, &s);
        writes_before = fake_ctrl_writes;
        script_init(&s); hello(&s, 1);
        {
            char extra[96];
            snprintf(extra, sizeof extra, ",\"observe_timing\":true,\"settle_wait_us\":%ld", requested);
            timing_capture(&s, 3, extra);
        }
        req(&s, 4, "device.info", "{\"observe_timing\":true}");
        timing_clock_calls = 0; sleep_calls = 0; sleep_total_us = 0; sleep_at_ctrl_writes = -1;
        check("waiting capture keeps transport usable", serve(d, &s) == 0);
        check("exactly one injected sleep of the requested duration",
              sleep_calls == 1 && sleep_total_us == (uint64_t)requested && last_sleep_us == (uint64_t)requested);
        check("the wait happens after the capture transaction began",
              sleep_at_ctrl_writes > writes_before && sleep_at_ctrl_writes < fake_ctrl_writes);
        check("a waiting capture adds exactly two clock reads",
              timing_clock_calls == base_clocks + 2u);
        check("request value is reported back", timing_number(&s, "settle_wait_requested_us") == (uint64_t)requested);
        check("settle brackets are present, ordered and inside the TX-on..RX-arm gap",
              timing_number(&s, "settle_begin_ns") >= timing_number(&s, "tx_on_end_ns") &&
              timing_number(&s, "settle_end_ns") >= timing_number(&s, "settle_begin_ns") &&
              timing_number(&s, "settle_end_ns") <= timing_number(&s, "rx_arm_begin_ns"));
        rf_daemon_destroy(d); rf_core_destroy(core);
    }
    /* No wait facility means NO wait: refuse rather than silently proceed. */
    {
        rf_daemon_config_t cfg; rf_core_t *core = healthy_core();
        script_init(&s); script_init(&s);
        core = core;
        fill_identity(&cfg); cfg.sleep_us = NULL;
        check("a daemon without a wait facility is constructible", rf_daemon_create(&cfg, &d) == 0);
        rf_daemon_attach_core(d, core);
        script_init(&s); timing_load(&s); serve(d, &s);
        { int writes = fake_ctrl_writes, spi = fake_spi_writes;
          script_init(&s); hello(&s, 1);
          timing_capture(&s, 3, ",\"observe_timing\":true,\"settle_wait_us\":1000");
          req(&s, 4, "device.info", "{\"observe_timing\":true}"); serve(d, &s);
          check("a wait without a wait facility is refused before any write",
                contains(&s, "INVALID_STATE") && !contains(&s, "\"type\":\"iq_frame\"") &&
                writes == fake_ctrl_writes && spi == fake_spi_writes);
        }
        rf_daemon_destroy(d); rf_core_destroy(core);
    }
    /* Malformed and unobservable requests are refused before hardware. */
    {
        static const char *const bad[] = {
            "\"1000\"", "-1", "null", "[]", "{}", "5001", "1000000"
        };
        for (j = 0; j < 7; ++j) {
            char extra[96]; int writes, spi;
            core = healthy_core(); d = new_daemon(core);
            script_init(&s); timing_load(&s); serve(d, &s);
            writes = fake_ctrl_writes; spi = fake_spi_writes;
            script_init(&s); hello(&s, 1);
            snprintf(extra, sizeof extra, ",\"observe_timing\":true,\"settle_wait_us\":%s", bad[j]);
            timing_capture(&s, 3, extra);
            serve(d, &s);
            check("malformed settle_wait_us refused without control writes",
                  contains(&s, "INVALID_ARGUMENT") && !contains(&s, "\"type\":\"iq_frame\"") &&
                  writes == fake_ctrl_writes && spi == fake_spi_writes);
            rf_daemon_destroy(d); rf_core_destroy(core);
        }
        core = healthy_core(); d = new_daemon(core);
        script_init(&s); timing_load(&s); serve(d, &s);
        { int writes = fake_ctrl_writes, spi = fake_spi_writes;
          script_init(&s); hello(&s, 1);
          timing_capture(&s, 3, ",\"settle_wait_us\":1000");
          serve(d, &s);
          check("a wait without observe_timing is refused before any write",
                contains(&s, "INVALID_ARGUMENT") && !contains(&s, "\"type\":\"iq_frame\"") &&
                writes == fake_ctrl_writes && spi == fake_spi_writes);
        }
        rf_daemon_destroy(d); rf_core_destroy(core);
    }
    /* The parameter belongs to physical capture only. */
    {
        static const char *const other[] = { "rx.capture", "tx.start", "device.info", "state.get" };
        for (j = 0; j < 4; ++j) {
            core = healthy_core(); d = new_daemon(core);
            script_init(&s); timing_load(&s); serve(d, &s);
            { int writes = fake_ctrl_writes;
              script_init(&s); hello(&s, 1);
              req(&s, 2, other[j], "{\"samples\":4,\"ratio\":0,\"settle_wait_us\":1000}");
              serve(d, &s);
              check("settle_wait_us is refused outside physical capture",
                    contains(&s, "INVALID_ARGUMENT") && writes == fake_ctrl_writes);
            }
            rf_daemon_destroy(d); rf_core_destroy(core);
        }
    }
    /* An unprovable wait does not publish a trace. */
    {
        core = healthy_core(); d = new_daemon(core);
        timing_ordered = true;
        script_init(&s); timing_load(&s);
        timing_capture(&s, 3, ",\"observe_timing\":true,\"settle_wait_us\":1000");
        req(&s, 4, "device.info", "{\"observe_timing\":true}"); serve(d, &s);
        check("an ordered waiting capture publishes its trace",
              timing_find(&s, "\"last_capture_timing\":{\"available\":true") != NULL);
        timing_ordered = false; timing_bad_at = 0;
        rf_daemon_destroy(d); rf_core_destroy(core);
    }
}
```

在 `main()` 里 `test_capture_timing();` 之后登记：

```c
    test_settle_wait();
```

并把既有的协议版本钉子改成新值：

```c
        check("hello declares the additive protocol revision",
              contains(&s, "\"protocol_minor\":5"));
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd rf_overlay/sw/board_app
gcc -I. -Ivendor -DPLATFORM_HOST -D_GNU_SOURCE -UWIN32 -include test/host_compat.h \
  -O1 -g -Wall -Wextra -Wno-unused-parameter \
  rf_pl_status_reader.c rf_daemon.c rf_daemon_main.c rf_protocol.c rf_state.c rf_iq.c \
  rf_core.c platform_rf.c selftest.c test/fake_pl.c \
  vendor/ad9361.c vendor/ad9361_api.c vendor/ad9361_conv.c vendor/util.c \
  test/test_rf_daemon.c -o .hostobj-s2-settle/test_rf_daemon.exe -lm
```
期望：编译失败（`sleep_us` 不存在、`settle_wait_us` 未实现、minor 钉子为 4）。
RED 日志 → `evidence/rf-teaching/s2/settle-wait-2026-10-07/c-settle-red.log`。

- [ ] **Step 3: 最小实现**

`rf_version.h`：

```c
/* Minor 5: bounded opt-in settling wait on physical capture.  Additive: a
 * request that omits it is byte-identical to minor 4 behaviour, and the
 * release manifest records the MAJOR, so no published manifest is invalidated. */
#define RF_PROTOCOL_MINOR 5u
```

`rf_daemon.h`（放在 `now_ns` 之后）：

```c
    /* Sleeps at least `us` microseconds.  Injected for the same reason as
     * now_ns: rf_daemon.c is linked directly by the host suite, so a real
     * nanosleep here could not be driven deterministically -- and "the wait
     * actually happened" is the whole point of this feature.
     *
     * NULL means "this process cannot wait".  A capture that asks for a
     * settling wait is then REFUSED, never silently run without one: an
     * unperformed wait that reports success is precisely the measurement this
     * exists to prevent.  Same fail-closed shape as verify_control_token. */
    void (*sleep_us)(uint64_t us);
```

`rf_daemon.c` —— 上限常量放在 `capture_timing_t` 附近：

```c
/* The ceiling on an opt-in settling wait.  Bounded so a request cannot become an
 * unbounded stall of the single control client. */
#define RF_SETTLE_WAIT_MAX_US 5000u
```

`capture_timing_t` 增量字段：

```c
typedef struct {
    bool available;
    long capture_request_id;
    uint64_t capture_timestamp_ns;
    unsigned samples, ratio;
    unsigned settle_wait_requested_us;
    uint64_t settle_begin_ns, settle_end_ns;
    uint64_t brackets[8];
} capture_timing_t;
```

`capture_timing_valid` 追加校验：

```c
    if (timing->capture_timestamp_ns < timing->brackets[7])
        return false;
    /* A requested wait must be PROVABLE: both brackets present, ordered, and
     * inside the TX-on..RX-arm gap they were taken from.  An unprovable wait is
     * not a trace we publish. */
    if (timing->settle_wait_requested_us &&
        (!timing->settle_begin_ns || !timing->settle_end_ns ||
         timing->settle_begin_ns < timing->brackets[1] ||
         timing->settle_end_ns < timing->settle_begin_ns ||
         timing->settle_end_ns > timing->brackets[2]))
        return false;
    return true;
```

`op_loopback_physical_capture` —— 在解析 `ratio` 的那段之后、`d->tx_count` 检查之前插入：

```c
    {
        long settle = 0;
        int st = opt_uint(rq, "settle_wait_us", &settle);
        if (st < 0 || (st == 0 && (settle < 0 || settle > (long)RF_SETTLE_WAIT_MAX_US)))
            return arg_error(c, rq, "settle_wait_us");
        if (st == 1)
            settle = 0;
        /* A wait nobody can measure is not evidence.  Requiring the observable
         * path makes "every wait is provable" structural rather than a promise. */
        if (settle > 0 && !observe_timing)
            return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                          "a settling wait must be observable: settle_wait_us "
                          "requires observe_timing");
        if (settle > 0 && !d->cfg.sleep_us)
            return refuse(c, rq, RF_PROTO_E_INVALID_STATE,
                          "this daemon has no wait facility");
        settle_us = settle;
    }
```
（函数顶部声明 `long settle_us = 0;`。）

等待插入点——在 `rf_core_prepare_rx` 之前：

```c
    /* The settling wait sits between the TX source coming on and the RX being
     * prepared, which is where a gain that has not settled would otherwise be
     * captured.  TX is sourcing for the whole wait, so the AGC has signal to
     * settle on.  Clock reads are taken only when a wait was requested, and a
     * requested wait always implies observe_timing (enforced above). */
    if (rc == RF_ERR_OK && settle_us > 0) {
        timing.settle_wait_requested_us = (unsigned)settle_us;
        timing.settle_begin_ns = d->cfg.now_ns();
        d->cfg.sleep_us((uint64_t)settle_us);
        timing.settle_end_ns = d->cfg.now_ns();
    }
    if (rc == RF_ERR_OK)
        rc = rf_core_prepare_rx(d->core, (uint8_t)ratio, samples, false);
```

`device.info` 序列化——在 8 个 bracket 循环之后、闭合 `}` 之前：

```c
        if (append(out, sizeof out, &used, ",\"settle_wait_requested_us\":%u",
                   timing->settle_wait_requested_us) != 0)
            return -1;
        if (timing->settle_wait_requested_us &&
            append(out, sizeof out, &used,
                   ",\"settle_begin_ns\":%llu,\"settle_end_ns\":%llu",
                   (unsigned long long)timing->settle_begin_ns,
                   (unsigned long long)timing->settle_end_ns) != 0)
            return -1;
```

`dispatch` 的 op 门禁——在既有 `observe_timing` 门禁之后：

```c
            if (strcmp(rq->op, "loopback.physical.capture") != 0) {
                long unused_wait;
                if (opt_uint(rq, "settle_wait_us", &unused_wait) != 1)
                    return refuse(c, rq, RF_PROTO_E_INVALID_ARGUMENT,
                                  "settle_wait_us is only supported by physical capture");
            }
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd rf_overlay/sw/board_app
HOST_OBJDIR=.hostobj-s2-settle make host-test host-test-core host-test-iq \
  host-test-protocol host-test-daemon 2>&1 | tail -40
python3 test/test_vendor_integrity.py
```
期望：全绿，exit 0；`test_rf_daemon` 中既有 348 项不回归，新增 settle 断言全部通过。
日志 → `evidence/rf-teaching/s2/settle-wait-2026-10-07/c-settle-green.log`。

- [ ] **Step 5: 记录源码摘要**

```bash
sha256sum rf_daemon.c rf_daemon.h rf_version.h test/test_rf_daemon.c
```
写入 `evidence/rf-teaching/s2/settle-wait-2026-10-07/c-settle-source-sha256.json`。

---

### Task 2: Python 客户端 — 参数、版本门禁与 trace 校验

**Files:**
- Modify: `pynq_overlay/sw/rf_status_client.py`
- Test: `pynq_overlay/sw/tests/test_rf_capture_timing.py`

**Interfaces:**
- Consumes: Task 1 的协议 minor 5、`settle_wait_us` 参数、`settle_wait_requested_us` / `settle_begin_ns` / `settle_end_ns` 字段。
- Produces:
  - `_capture_args` 接受 physical 的 `settle_wait_us`
  - `_capture_timing_observation` 返回的 `last_capture_timing` 增加 `settle_wait_requested_us`，条件增加 `settle_begin_ns` / `settle_end_ns`
  - 常量 `SETTLE_WAIT_MAX_US = 5000`

- [ ] **Step 1: 写失败测试**

在 `pynq_overlay/sw/tests/test_rf_capture_timing.py` 追加：

```python
SETTLE_MAX = 5000


def test_settle_wait_is_physical_only(client):
    wire = client._capture_args({"samples": 4096, "ratio": 0, "settle_wait_us": 1000},
                                physical=False)
    assert "settle_wait_us" not in wire


@pytest.mark.parametrize("bad", [True, 1.5, "1000", None, [], {}])
def test_settle_wait_rejects_non_integer(client, bad):
    with pytest.raises(StatusProtocolError):
        client._capture_args({"samples": 4096, "ratio": 0, "cable_confirmed": True,
                              "attenuation_mdB": 40000, "observe_timing": True,
                              "settle_wait_us": bad}, physical=True)


@pytest.mark.parametrize("bad", [-1, SETTLE_MAX + 1, 1000000])
def test_settle_wait_rejects_out_of_range(client, bad):
    with pytest.raises(StatusProtocolError):
        client._capture_args({"samples": 4096, "ratio": 0, "cable_confirmed": True,
                              "attenuation_mdB": 40000, "observe_timing": True,
                              "settle_wait_us": bad}, physical=True)


def test_settle_wait_requires_observe_timing(client):
    with pytest.raises(StatusProtocolError):
        client._capture_args({"samples": 4096, "ratio": 0, "cable_confirmed": True,
                              "attenuation_mdB": 40000, "settle_wait_us": 1000},
                             physical=True)


def test_settle_wait_is_accepted_at_the_bound(client):
    wire = client._capture_args({"samples": 4096, "ratio": 0, "cable_confirmed": True,
                                 "attenuation_mdB": 40000, "observe_timing": True,
                                 "settle_wait_us": SETTLE_MAX}, physical=True)
    assert wire["settle_wait_us"] == SETTLE_MAX


def test_settle_wait_is_refused_before_send_on_an_old_daemon(client):
    client._protocol_minor_seen = 4
    with pytest.raises(StatusProtocolError, match="minor"):
        client.capture({"samples": 4096, "ratio": 0, "cable_confirmed": True,
                        "attenuation_mdB": 40000, "observe_timing": True,
                        "settle_wait_us": 1000}, physical=True)
```

并扩展既有的 trace 校验测试：请求 1000 时缺 `settle_begin_ns` 必须拒绝；`settle_end_ns < settle_begin_ns` 必须拒绝；`settle_end_ns > rx_arm_begin_ns` 必须拒绝；`settle_wait_requested_us` 与请求值不符必须拒绝；`settle_wait_requested_us == 0` 时出现 bracket 必须拒绝。

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest pynq_overlay/sw/tests/test_rf_capture_timing.py -q
```
期望：新用例 FAIL。RED 日志 → `.../python-settle-red.log`。

- [ ] **Step 3: 最小实现**

模块常量：

```python
SETTLE_WAIT_MAX_US = 5000
```

`_capture_args` 的 physical 分支，在 `observe_timing` 处理之后：

```python
            if "settle_wait_us" in args:
                wait = args["settle_wait_us"]
                if type(wait) is not int or not 0 <= wait <= SETTLE_WAIT_MAX_US:
                    raise StatusProtocolError(
                        "capture settle_wait_us must be an integer 0..%d"
                        % SETTLE_WAIT_MAX_US)
                if wait and args.get("observe_timing") is not True:
                    raise StatusProtocolError(
                        "a settling wait requires observe_timing: an unmeasured "
                        "wait is not evidence")
                wire["settle_wait_us"] = wait
```

`_capture_args` 的 `allowed` 集合加入 `"settle_wait_us"`：

```python
            allowed |= {"cable_confirmed", "attenuation_mdB", "observe_timing",
                        "settle_wait_us"}
```

`_capture_unlocked` 的版本门禁，在既有 `observe_timing` 门禁之后：

```python
        if wire.get("settle_wait_us", 0) > 0 and (
                self._session_socket is None or self._protocol_minor_seen is None or
                self._protocol_minor_seen < 5):
            raise StatusProtocolError(
                "capture settling wait requires protocol minor >= 5 confirmed "
                "by the live hello", "UNSUPPORTED")
```

`_capture_timing_observation`，把 minor 门禁提到 5：

```python
    if identity["protocol_minor"] < 5 or "last_capture_timing" not in info:
        return dict(identity, last_capture_timing=None, reason="UNSUPPORTED")
```

并在 8 个 bracket 校验之后追加：

```python
    wait_us = integer("settle_wait_requested_us", 0, SETTLE_WAIT_MAX_US)
    if wait_us != int(requested_wait_us):
        raise StatusProtocolError("capture settling wait does not match the request")
    checked["settle_wait_requested_us"] = wait_us
    if wait_us:
        begin = integer("settle_begin_ns")
        end = integer("settle_end_ns")
        if not checked["tx_on_end_ns"] <= begin <= end <= checked["rx_arm_begin_ns"]:
            raise StatusProtocolError(
                "settle bracket is outside the TX-on..RX-arm gap")
        checked["settle_begin_ns"] = begin
        checked["settle_end_ns"] = end
    elif "settle_begin_ns" in trace or "settle_end_ns" in trace:
        raise StatusProtocolError("a zero settling wait must not report brackets")
```
函数签名改为 `def _capture_timing_observation(info, frame, requested_wait_us=0):`，并在 `observe_capture_timing(frame, requested_wait_us=0)` 中透传。

- [ ] **Step 4: 跑测试确认通过**

```bash
python -m pytest pynq_overlay/sw/tests/test_rf_capture_timing.py -q
python -m pytest pynq_overlay/sw/tests/ -q
```
期望：新用例通过，既有客户端与 pynq 套件不回归。日志 → `.../python-settle-green.log`。

---

### Task 3: 实验工具 — 等待档位对照（`wait_campaign`）

**Files:**
- Modify: `scripts/rf_capture_timing_probe.py`
- Test: `scripts/tests/test_rf_capture_timing_probe.py`

**Interfaces:**
- Consumes: Task 1/2 的 wire 字段与 trace 字段；既有 `window_statistics`、`campaign`、`live_factory`、`EnvironmentSession`。
- Produces:
  - `SETTLE_WAIT_LEVELS = (0, 1000)`
  - `validate_diagnostic(row, output, release, kind, settle_wait_us=0)`（新可选参数，既有调用不变）
  - `wait_campaign(factory, output, release, levels=SETTLE_WAIT_LEVELS)`
  - `live_factory(...)` 的会话支持 `capture(tag, physical, settle_wait_us)`
  - CLI `--wait-levels 0,1000`

- [ ] **Step 1: 写失败测试**

在 `scripts/tests/test_rf_capture_timing_probe.py` 追加（沿用该文件既有的 `Session` 替身风格）：

```python
def test_wait_campaign_runs_the_declared_grid_in_order(tmp_path):
    seen = []

    class S:
        def __init__(self, *a): pass
        def initial(self): return {"safe": True, "release_id": "rel"}
        def setup(self, seed): pass
        def scene(self, kind): pass
        def capture(self, tag, physical, settle_wait_us=0):
            seen.append((tag, settle_wait_us)); return _row(tag, settle_wait_us)
        def cleanup(self): return {"safe": True, "disarm_ack": True}
        def close(self): pass

    result = P.wait_campaign(lambda: S(), tmp_path, "rel")
    assert [t for t, _ in seen] == [
        "s00-normal-w0000", "s00-normal-w1000",
        "s00-narrowband-w0000", "s00-narrowband-w1000",
        "s01-normal-w0000", "s01-normal-w1000",
        "s01-narrowband-w0000", "s01-narrowband-w1000"]
    assert [w for _, w in seen] == [0, 1000] * 4
    assert result["passed"] is True and len(result["records"]) == 8
    assert len(result["sessions"]) == 2


def test_wait_campaign_rejects_unbounded_or_unsorted_levels(tmp_path):
    for bad in ((0, 6000), (1000, 0), (0, 0), (), (0, -1)):
        with pytest.raises(ValueError):
            P.wait_campaign(lambda: None, tmp_path / repr(bad), "rel", bad)


def test_wait_campaign_stops_on_clipping(tmp_path):
    class S:
        def __init__(self, *a): pass
        def initial(self): return {"safe": True, "release_id": "rel"}
        def setup(self, seed): pass
        def scene(self, kind): pass
        def capture(self, tag, physical, settle_wait_us=0):
            return _row(tag, settle_wait_us, rail=1)
        def cleanup(self): return {"safe": True, "disarm_ack": True}
        def close(self): pass

    result = P.wait_campaign(lambda: S(), tmp_path, "rel")
    assert result["passed"] is False and result["stop_cause"] == "CLIPPED"
    assert len(result["records"]) == 1


def test_diagnostic_rejects_a_wait_that_does_not_match_the_request():
    with pytest.raises(ValueError):
        P.validate_diagnostic({"release_id": "rel", "egress_safe": True,
                               "settle_wait_requested_us": 1000},
                              Path("."), "rel", "normal", settle_wait_us=0)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest scripts/tests/test_rf_capture_timing_probe.py -q
```
期望：`AttributeError: module has no attribute 'wait_campaign'`。RED 日志 → `.../probe-settle-red.log`。

- [ ] **Step 3: 最小实现**

顶部常量与文档串补充：

```python
SETTLE_WAIT_LEVELS = (0, 1000)
SETTLE_WAIT_MAX_US = 5000
```

`validate_diagnostic` 增加可选参数，并在既有 identity/IQ 交叉校验之后追加：

```python
def validate_diagnostic(row, output, release, kind, settle_wait_us=0):
```
```python
        if row.get('settle_wait_requested_us') != settle_wait_us:
            raise ValueError('settling wait request does not match this frame')
        requested = trace.get('settle_wait_requested_us')
        if requested != settle_wait_us:
            raise ValueError('settling wait was not recorded by the daemon')
        if settle_wait_us:
            begin = trace.get('settle_begin_ns'); end = trace.get('settle_end_ns')
            if (type(begin) is not int or type(end) is not int or begin <= 0 or end < begin
                    or not times[1] <= begin <= end <= times[2]):
                raise ValueError('settle bracket is outside the TX-on..RX-arm gap')
        elif 'settle_begin_ns' in trace or 'settle_end_ns' in trace:
            raise ValueError('a zero settling wait must not report brackets')
```
返回字典追加：

```python
            settle_wait_requested_us=settle_wait_us,
            settle_actual_ns=(trace['settle_end_ns'] - trace['settle_begin_ns']) if settle_wait_us else 0,
```

`live_factory` 的 `TimedClient` / `TimingSession` 改为按帧携带等待值：

```python
class TimedClient:
    def __init__(self,inner,wait):
        self.inner=inner; self.wait=wait
    def __getattr__(self,name):return getattr(self.inner,name)
    def capture(self,args,physical=False):
        args=dict(args)
        if physical:
            args['observe_timing']=True
            if self.wait[0]:args['settle_wait_us']=self.wait[0]
        return self.inner.capture(args,physical=physical)
```
```python
    class TimingSession(EnvironmentSession):
        def __init__(self):
            super().__init__(expected,output)
            self._wait=[0]
            self.client.client=TimedClient(self.client.client,self._wait)
        def capture(self,tag,physical,settle_wait_us=0):
            self._wait[0]=settle_wait_us
            diag=dict(scope=TIMING_SCOPE,settle_wait_requested_us=settle_wait_us,
                instrumentation=('no inserted settling wait' if not settle_wait_us else
                    '%d us requested settling wait before RX prepare'%settle_wait_us))
```
并在 `row['timing_file']=tag+'.timing.json'` 之前加 `row['settle_wait_requested_us']=settle_wait_us`。

新增 `wait_campaign`：

```python
def wait_campaign(factory,output,release,levels=SETTLE_WAIT_LEVELS):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    path=output/'result.json'
    if path.exists():raise FileExistsError(str(path))
    if (type(levels) is not tuple or not levels
            or any(type(v) is not int or not 0<=v<=SETTLE_WAIT_MAX_US for v in levels)
            or list(levels)!=sorted(set(levels))):
        raise ValueError('bounded, strictly increasing settle wait levels required')
    rows,sessions,attempts,seen_identity,seen_iq=[],[],[],set(),set()
    cause=None
    for index,seed in enumerate((1,37)):
        session=None;owned=closed=ack=False;error=cleanup_error=close_error=None
        try:
            session=factory();initial=session.initial()
            if initial.get('safe') is not True or initial.get('release_id')!=release:
                raise RuntimeError('initial_not_safe_or_wrong_release')
            owned=True;session.setup(seed)
            for kind in ('normal','narrowband'):
                session.scene(kind)
                for level in levels:
                    tag='s%02d-%s-w%04d'%(index,kind,level)
                    attempts.append(dict(tag=tag,truth=kind,session_index=index,
                        reference_seed=seed,settle_wait_us=level))
                    row=session.capture(tag,True,level)
                    row.update(tag=tag,truth=kind,session_index=index,reference_seed=seed,
                        settle_wait_us=level,actual_scene_prbs7_seed=1,
                        split='development-settle',origin='new instrumented physical TX1-RX1 capture')
                    rows.append(row)
                    checked=validate_diagnostic(row,output,release,kind,level)
                    row['diagnostic_check']=checked
                    identity=tuple(checked['identity']);digest=checked['iq_sha256']
                    if identity in seen_identity or digest in seen_iq:
                        raise ValueError('duplicate original frame')
                    seen_identity.add(identity);seen_iq.add(digest)
                    if (checked['statistics']['rail_components']
                            or (row.get('environment') or {}).get('reason')=='CLIPPED'):
                        cause='CLIPPED';break
                if cause is not None:break
        except Exception as exc:cause=type(exc).__name__;error=str(exc)
        finally:
            if owned:
                try:
                    cleanup=session.cleanup();closed=cleanup.get('safe') is True;ack=cleanup.get('disarm_ack') is True
                except Exception as exc:cleanup_error=str(exc)
            if session is not None:
                try:session.close()
                except Exception as exc:close_error=str(exc)
            if cause is None and (cleanup_error or close_error):cause='cleanup_unconfirmed'
            sessions.append(dict(index=index,reference_seed=seed,owned=owned,closed=closed,
                disarm_ack=ack,error=error,cleanup_error=cleanup_error,close_error=close_error,cause=cause))
        if cause is not None or error is not None:break
        if not closed or not ack:cause='close_unconfirmed';break
    expected=len(levels)*2*2
    result=dict(expected_release=release,
        scope='bounded single-variable settling wait; API brackets not sample edges',
        split='development-settle',levels=list(levels),records=rows,sessions=sessions,
        attempts=attempts,stop_cause=cause,
        passed=cause is None and len(rows)==expected and len(sessions)==2
            and all(s['closed'] and s['disarm_ack']
                and not any(s.get(k) for k in ('error','cleanup_error','close_error')) for s in sessions),
        formal_physical='NOT_RUN',human_acceptance='NOT_RUN',s2_stage_passed=False)
    with path.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2,allow_nan=False)
    return result
```

CLI 增加 `--wait-levels`（缺省 `0,1000`）并走 `wait_campaign`；**保留** 既有 `campaign` 与 `--expected-release/--output/--cable-confirmed` 行为不变。

- [ ] **Step 4: 跑测试确认通过**

```bash
python -m pytest scripts/tests/test_rf_capture_timing_probe.py -q
python -m pytest scripts/tests/ -q
```
期望：全绿，既有 4 帧 `campaign` 用例不回归。日志 → `.../probe-settle-green.log`。

---

### Task 4: 完整离线验证、独立审阅与阶段记录

**Files:**
- Modify: `CLAUDE.md`（当前状态段）
- Create: `docs/reports/2026-10-07-s2-settle-wait-offline.md`
- Create: `evidence/rf-teaching/s2/settle-wait-2026-10-07/stage-checkpoint.json`

**Interfaces:**
- Consumes: Task 1–3 的全部产物与日志。
- Produces: 离线阶段收据与报告。

- [ ] **Step 1: 全量离线验证**

```bash
python -m pytest pynq_overlay/sw/tests/ -q            # 板载软件（上一轮基线 1845 passed / 4 skipped）
python -m pytest host/tests/ -q                       # 上位机
python -m pytest scripts/tests/ -q
cd rf_overlay/sw/board_app && HOST_OBJDIR=.hostobj-s2-settle make host-test host-test-core \
  host-test-iq host-test-protocol host-test-daemon && python3 test/test_vendor_integrity.py
```
逐条记录真实数字，**不四舍五入、不替换**。日志 → `.../full-regression.log`。

- [ ] **Step 2: 搜索并更新所有协议 minor 钉子**

```bash
grep -rn "protocol_minor\|protocol_minor_seen\|minor.*4\|MINOR" \
  pynq_overlay/sw/rf_status_client.py scripts/rf_capture_timing_probe.py \
  rf_overlay/sw/board_app/rf_version.h rf_overlay/sw/board_app/test/test_rf_daemon*.c \
  pynq_overlay/sw/tests/ scripts/tests/ | grep -v "minor.*5"
```
对每一处判断：确实表示"本协议修订号"的钉到 5；表示历史帧或其它语义的**不动**，并在报告里逐条说明判断依据。

- [ ] **Step 3: 独立规范审阅**

派一个未参与实现的新代理，只依据 spec 与源码独立复核：断言覆盖、边界（0 / 5000 / 5001 / 非整数 / 缺 observe_timing / NULL sleep_us / 非 physical op）、缺省路径零睡眠零额外时钟读、trace 可证明性与失败不发布、旧路径逐位不变。**每个承重断言做一次变异并记录失败项**，报告实测数字而非采信作者摘要。

- [ ] **Step 4: 独立质量审阅**

派另一个新代理复跑全量离线测试与 C 全部目标，核对源码 SHA 与日志，独立确认没有放宽任何既有门槛。

- [ ] **Step 5: 写报告与阶段收据**

报告须明确写出：本轮**只完成离线设计、实现与验证**；`settle_wait_us > 0` 要求 `observe_timing`；缺省/零等待与既有行为逐位一致；协议 minor 升到 5；**native ARM 构建、安装、实采、S2 正式 freeze/108/12/真人、S3 一律 NOT_RUN**；削顶根因仍未证明，本轮不宣称任何发现。

`stage-checkpoint.json` 绑定：三个任务的源码 SHA、RED/GREEN 日志、全量回归真实数字、两轮独立审阅结论、明确的 NOT_RUN 列表。

- [ ] **Step 6: 更新 `CLAUDE.md` 当前状态段**

把"下一项为有上限的0/1000微秒单变量稳定等待对照设计，当前未添加等待或修改gain/AGC/判据"替换为：**等待已实现并离线验证完成，尚未上板**；下一步待用户批准后做 native ARM 构建、安装与最多 8 帧实采。

---

## 执行记录（2026-10-07，离线）

**Task 1 C守护进程 — 完成。** RED: 3 errors（全是缺失的新特性）。GREEN: exit 0，`test_rf_daemon` 783 PASS / 2 FAIL，vendor integrity 10 files passed。那 2 条 FAIL 与上一轮（已独立审阅通过）日志**逐条相同**（744/2），属 `test_selftest` 对假 PL 的自检既有结果；该目标只链接 `platform_rf.c`/`selftest.c`/`fake_pl.c`，结构性上不受本次改动影响。新增 39 条断言全过。`rf_daemon.c`/`rf_daemon_main.c` 告警数与上一轮一致（3/0），无新增。

**Task 2 Python客户端 — 完成。** RED: 16 failed / 149 passed。GREEN: `test_rf_capture_timing.py` **165 passed**；全量板载软件 **1870 passed / 4 skipped**。

**Task 3 实验工具 — 完成。** RED: 7 failed / 18 passed。GREEN: `test_rf_capture_timing_probe.py` **25 passed**；`test_rf_gain_candidate.py` 15 passed（含新增配对用例）。

### 执行中对本计划做出的四处修正

1. **minor 门禁纠正。** 原计划 Step 3 要把 `_capture_timing_observation` 的门禁从 `>=4` 提到 `>=5`，并让 `validate_diagnostic` 固定要求 `'1.0.2'/4`。两者都是错的：时序trace本身是minor-4特性，提到5会破坏对minor-4 daemon的兼容，并让已归档的旧4帧证据无法离线复验。改为：基础trace保持 `>=4`；仅 `settle_wait_us > 0` 时要求 `>=5`。`validate_diagnostic` 改用哨兵 `settle_wait_us=None` 表示"minor-4daemon、无等待字段"，`0` 表示"minor-5daemon、零等待"，两种形状严格区分。

2. **daemon版本必须同步升到 `1.0.3`**（原计划完全未提）。依据是两条硬证据：`scripts/rf_environment_candidate.py` 的硬编码配对表 `{'1.0.1':3,'1.0.2':4}`，以及 `rf_daemon_main.c` 启动时拿manifest的 `daemon_version` 比对二进制。不升版则新候选会被发布工具直接拒绝。已加入 `'1.0.3':5` 并补两条候选门禁用例。

3. **补上生产接线**（原计划遗漏 `rf_daemon_main.c`）。不接 `sleep_us` 则板上每次都拒绝等待，功能等于死的。已实现 `nanosleep`（EINTR续睡，避免信号缩短等待而trace仍报请求值）并接线。⚠ **该函数落在 `#ifndef PLATFORM_HOST` 区内，MinGW 编不到，本轮无法离线编译验证** —— 这是本阶段如实标注的验证缺口，只能由上板阶段的原生ARM构建闭合。

4. **客户端改为记住实发值**，而不是由调用方传入等待值。原设计的 `observe_capture_timing(frame, requested_wait_us=...)` 让调用方可以传一个与实际发送不符的数字，校验形同虚设。改为 `_capture_unlocked` 在捕获成功时记下 `self._capture_settle_wait_us`，`observe_capture_timing(frame)` 签名不变。

### 本阶段 NOT_RUN

native ARM 构建、安装、实采、S2 正式 freeze/108实板/12离线TEST、真人验收、S3 —— **全部 NOT_RUN**。削顶根因未证明，本轮不宣称任何发现。

---

## Self-Review

**Spec 覆盖：** 请求参数与边界 → Task 1 Step 3 + Task 2 Step 3；注入睡眠与 NULL fail-closed → Task 1 Step 3；trace 增量字段与顺序约束 → Task 1 Step 3 + Task 2 Step 3；协议 minor → Task 1 Step 3 + Task 4 Step 2；客户端校验与发送前门禁 → Task 2；8 帧对照设计 → Task 3；离线验证与 NOT_RUN 声明 → Task 4。无缺口。

**占位符扫描：** 无 TBD/TODO；每个代码步骤都给出可编译/可运行的完整片段。

**类型一致性：** `sleep_us` 在 Task 1 定义为 `void (*)(uint64_t)`，测试替身 `test_sleep_us(uint64_t)` 一致；`settle_wait_us` 在 C 为 `long`（解析后存 `unsigned`）、在 wire 为整数、在 Python 为 `int`；`settle_wait_requested_us` 三处同名同义；`validate_diagnostic` 的新参数带默认值，既有 4 帧调用不受影响。
