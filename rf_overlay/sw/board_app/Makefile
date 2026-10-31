# AD9361 TX bring-up application.
#
# Targets:
#   make            -- the on-board binary (native gcc on the Zynq's ARM core)
#   make host-test  -- the same platform layer + the same self-check, compiled on
#                      the development host against a C model of the PL and of
#                      the AD9361's SPI decode.  Catches instruction-encoding
#                      bugs before anything touches a board.
#   make host-test-iq -- pure host tests for the IQ wire/BRAM conversion contract
#   make host-test-protocol -- pure host tests for the NDJSON wire format
#   make host-test-daemon -- protocol + state machine + framing against fake PL
#   make vendor-integrity -- verify every checked-in vendor file
#
# -UWIN32 on the host target is not optional: vendor/util.h has
#   #ifdef WIN32 / #include "basetsd.h", which does not exist under MinGW.

# CC uses := rather than ?= on purpose.  GNU make has a BUILT-IN default of
# CC = cc, so `CC ?= gcc` never fires and the recipe silently compiles with
# `cc`.  That happens to work (cc is a symlink to gcc on the board and on any
# dev box), but relying on a symlink to pick the compiler is exactly the kind of
# thing that breaks on a different image.  := pins it.
CC       := gcc
CFLAGS   ?= -O2 -g -Wall -Wextra -Wno-unused-parameter
CPPFLAGS += -I. -Ivendor

VENDOR_SRC := vendor/ad9361.c vendor/ad9361_api.c vendor/ad9361_conv.c vendor/util.c
APP_SRC    := platform_rf.c selftest.c rf_main.c rf_core.c
OBJDIR     := .obj
OBJS       := $(addprefix $(OBJDIR)/,$(notdir $(VENDOR_SRC:.c=.o) $(APP_SRC:.c=.o)))

BIN := board_app

# Declared here, NOT next to its recipe further down.  make expands a rule's
# prerequisites when it READS the rule, and `all` (below) reads $(DAEMON_BIN)
# long before a := assignment down by the daemon's recipe would have run -- so
# leaving it there made the prerequisite empty and a plain `make` silently built
# only $(BIN), exit 0.  Anything `all` depends on is assigned above it.
DAEMON_BIN := rf_daemon

.PHONY: all clean host-test host-test-core host-test-iq host-test-protocol \
        host-test-daemon host-test-sleep host-test-transport vendor-integrity

all: $(BIN) $(DAEMON_BIN)

$(OBJDIR):
	mkdir -p $(OBJDIR)

$(OBJDIR)/%.o: %.c | $(OBJDIR)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

$(OBJDIR)/%.o: vendor/%.c | $(OBJDIR)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

$(BIN): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $(OBJS) -lrt -lm

# ---------------------------------------------------------------- host tests
HOSTCC      ?= gcc
HOST_OBJDIR := .hostobj
HOST_CPPFLAGS := -I. -Ivendor -DPLATFORM_HOST -D_GNU_SOURCE -UWIN32 -include test/host_compat.h
HOST_CFLAGS   ?= -O1 -g -Wall -Wextra -Wno-unused-parameter
HOST_SRC      := platform_rf.c selftest.c test/fake_pl.c
HOST_OBJS     := $(addprefix $(HOST_OBJDIR)/,$(notdir $(HOST_SRC:.c=.o)))

host-test: $(HOST_OBJDIR)/test_platform_rf $(HOST_OBJDIR)/test_selftest host-test-daemon
	@echo "=== host unit tests ==="
	$(HOST_OBJDIR)/test_platform_rf
	@echo "=== host self-check (same code the board runs) ==="
	$(HOST_OBJDIR)/test_selftest

host-test-core: $(HOST_OBJDIR)/test_rf_core
	@echo "=== host RF core tests ==="
	$(HOST_OBJDIR)/test_rf_core

$(HOST_OBJDIR)/test_rf_core: rf_core.c rf_core.h platform_rf.c platform_rf.h selftest.c test/fake_pl.c test/fake_pl.h test/test_rf_core.c $(VENDOR_SRC) | $(HOST_OBJDIR)
	$(HOSTCC) $(HOST_CPPFLAGS) $(HOST_CFLAGS) rf_core.c platform_rf.c selftest.c test/fake_pl.c $(VENDOR_SRC) test/test_rf_core.c -o $@ -lm

# IQ conversion tests deliberately link only the pure conversion module and do
# not pull in the platform test's fake hardware or vendor driver.
host-test-iq: $(HOST_OBJDIR)/test_rf_iq
	@echo "=== host IQ conversion tests ==="
	$(HOST_OBJDIR)/test_rf_iq

$(HOST_OBJDIR)/test_rf_iq: rf_iq.c rf_iq.h platform_rf.h test/test_rf_iq.c | $(HOST_OBJDIR)
	$(HOSTCC) $(HOST_CPPFLAGS) $(HOST_CFLAGS) rf_iq.c test/test_rf_iq.c -o $@

# The bounded sleep is compiled into BOTH the board binary and this host test on
# purpose.  Its logic used to sit behind the PLATFORM_HOST guard in
# rf_daemon_main.c, where MinGW could not compile it at all -- so "the wait
# completes or reports failure" was asserted about code no host build ever saw.
# Deliberately narrow link: no vendor include, no PLATFORM_HOST, no fake
# hardware, so a syscall creeping into rf_sleep.c would fail to link.
host-test-sleep: $(HOST_OBJDIR)/test_rf_sleep
	@echo "=== host bounded-sleep tests ==="
	$(HOST_OBJDIR)/test_rf_sleep

$(HOST_OBJDIR)/test_rf_sleep: rf_sleep.c rf_sleep.h test/test_rf_sleep.c | $(HOST_OBJDIR)
	$(HOSTCC) -I. $(HOST_CFLAGS) rf_sleep.c test/test_rf_sleep.c -o $@

# Deliberately narrower than HOST_CPPFLAGS: no vendor include, no PLATFORM_HOST,
# no compat shim.  If rf_protocol.c ever grew a call into the platform or core
# layer this target would stop linking -- which is the plan's "the parser
# performs no hardware callback", enforced rather than asserted.
host-test-protocol: $(HOST_OBJDIR)/test_rf_protocol
	@echo "=== host protocol tests ==="
	$(HOST_OBJDIR)/test_rf_protocol

$(HOST_OBJDIR)/test_rf_protocol: rf_protocol.c rf_protocol.h test/test_rf_protocol.c | $(HOST_OBJDIR)
	$(HOSTCC) -I. $(HOST_CFLAGS) rf_protocol.c test/test_rf_protocol.c -o $@

# Two binaries, and the split matters.  test_rf_state links ONLY rf_state.c --
# no PLATFORM_HOST, no vendor, no fake hardware -- so a state decision that
# reached for a register or an SPI transfer would fail to link rather than be
# caught by a spy.  test_rf_daemon is the opposite: it drives the real framing
# loop over a scripted transport and a real core over the fake PL.
host-test-daemon: $(HOST_OBJDIR)/test_rf_state $(HOST_OBJDIR)/test_rf_daemon \
                   $(HOST_OBJDIR)/test_rf_daemon_main host-test-transport host-test-headroom
	$(HOST_OBJDIR)/test_rf_state
	@echo "=== host daemon framing tests ==="
	$(HOST_OBJDIR)/test_rf_daemon
	@echo "=== host daemon startup-order tests ==="
	$(HOST_OBJDIR)/test_rf_daemon_main

$(HOST_OBJDIR)/test_rf_state: rf_state.c rf_state.h rf_core.h rf_protocol.h \
                              test/test_rf_state.c | $(HOST_OBJDIR)
	$(HOSTCC) -I. $(HOST_CFLAGS) rf_state.c test/test_rf_state.c -o $@

# rf_daemon_main.c is linked in as well, and deliberately: the daemon's
# device.info must report the release identity, and that identity reaches the
# daemon through rf_daemon_startup's manifest check.  Compiling only the "correct
# answer" into the test would leave the manifest->config plumbing unexercised, so
# the test drives the real startup function with an injected core opener and a
# real manifest file on disk.
$(HOST_OBJDIR)/test_rf_daemon: rf_pl_status_reader.c rf_pl_status_reader.h rf_daemon.c rf_daemon.h rf_headroom.c rf_headroom.h rf_sha256.c rf_sha256.h rf_daemon_main.c \
                               rf_daemon_main.h rf_transport_policy.c rf_transport_policy.h rf_protocol.c \
                               rf_protocol.h rf_state.c rf_state.h rf_iq.c \
                               rf_iq.h rf_sleep.c rf_sleep.h rf_core.c rf_core.h platform_rf.c \
                               platform_rf.h selftest.c test/fake_pl.c \
                               test/fake_pl.h test/test_rf_daemon.c \
                               $(VENDOR_SRC) | $(HOST_OBJDIR)
	$(HOSTCC) $(HOST_CPPFLAGS) $(HOST_CFLAGS) rf_pl_status_reader.c rf_daemon.c rf_headroom.c rf_sha256.c rf_daemon_main.c rf_transport_policy.c \
	  rf_protocol.c rf_state.c rf_iq.c rf_sleep.c rf_core.c platform_rf.c selftest.c \
	  test/fake_pl.c $(VENDOR_SRC) test/test_rf_daemon.c -o $@ -lm

# rf_daemon_main.c defines the daemon's executable entry point.  It is compiled
# into this host test with PLATFORM_HOST, which excludes main() and the POSIX
# socket transport: what is tested is the startup ORDER (manifest before any
# hardware), and the test injects the hardware side so "no register was written"
# is asserted rather than read off the source.
$(HOST_OBJDIR)/test_rf_daemon_main: rf_daemon_main.c rf_transport_policy.c rf_transport_policy.h rf_daemon.h rf_core.h \
                                    rf_version.h platform_rf.h \
                                    test/test_rf_daemon_main.c | $(HOST_OBJDIR)
	$(HOSTCC) $(HOST_CPPFLAGS) $(HOST_CFLAGS) rf_daemon_main.c rf_transport_policy.c \
	  test/test_rf_daemon_main.c -o $@

# Portable production transport policy with injected syscall outcomes. No
# PLATFORM_HOST guard: the board and this test link the identical policy.
# Included by host-test-daemon and thus host-test/ARM build receipt targets.
host-test-transport: $(HOST_OBJDIR)/test_rf_transport_policy
	@echo "=== host transport policy tests ==="
	$(HOST_OBJDIR)/test_rf_transport_policy

$(HOST_OBJDIR)/test_rf_transport_policy: rf_transport_policy.c rf_transport_policy.h test/test_rf_transport_policy.c | $(HOST_OBJDIR)
	$(HOSTCC) -I. $(HOST_CFLAGS) rf_transport_policy.c test/test_rf_transport_policy.c -o $@

# ----------------------------------------------------------------- board binary
# The board executable: the same sources plus the daemon's main() and the TCP
# transport (both board-only).  Built natively with the board's gcc, like the CLI.
# DAEMON_BIN itself is assigned near the top, above `all`, which needs it.
DAEMON_SRC := rf_headroom.c rf_sha256.c rf_pl_status_reader.c rf_daemon_main.c rf_daemon.c rf_protocol.c rf_state.c rf_iq.c \
              rf_sleep.c rf_transport_policy.c rf_core.c platform_rf.c selftest.c $(VENDOR_SRC)
# $(filter %.c,...) rather than a blind .c=.o substitution over the whole _SRC
# list: this variable is a list of SOURCES, and a header that slipped into it
# would otherwise become ".obj/x.h" -- a prerequisite no rule can make, which is
# exactly how `make rf_daemon` failed ("No rule to make target '.obj/x.h'").
DAEMON_OBJS := $(addprefix $(OBJDIR)/,$(notdir $(patsubst %.c,%.o,$(filter %.c,$(DAEMON_SRC)))))

$(DAEMON_BIN): $(DAEMON_OBJS)
	$(CC) $(CFLAGS) -o $@ $(DAEMON_OBJS) -lrt -lm

vendor-integrity:
	python3 test/test_vendor_integrity.py

$(HOST_OBJDIR):
	mkdir -p $(HOST_OBJDIR)

$(HOST_OBJDIR)/%.o: %.c | $(HOST_OBJDIR)
	$(HOSTCC) $(HOST_CPPFLAGS) $(HOST_CFLAGS) -c $< -o $@

$(HOST_OBJDIR)/%.o: test/%.c | $(HOST_OBJDIR)
	$(HOSTCC) $(HOST_CPPFLAGS) $(HOST_CFLAGS) -c $< -o $@

$(HOST_OBJDIR)/test_platform_rf: $(HOST_OBJS) $(HOST_OBJDIR)/test_platform_rf.o
	$(HOSTCC) $(HOST_CFLAGS) -o $@ $^

$(HOST_OBJDIR)/test_selftest: $(HOST_OBJS) $(HOST_OBJDIR)/test_selftest.o
	$(HOSTCC) $(HOST_CFLAGS) -o $@ $^

clean:
	rm -rf $(OBJDIR) $(HOST_OBJDIR) $(BIN) $(DAEMON_BIN)

host-test-headroom: $(HOST_OBJDIR)/test_rf_headroom $(HOST_OBJDIR)/test_rf_headroom_owned
	$(HOST_OBJDIR)/test_rf_headroom
	$(HOST_OBJDIR)/test_rf_headroom_owned
$(HOST_OBJDIR)/test_rf_headroom: $(DAEMON_SRC) test/test_rf_headroom.c test/test_rf_daemon.c rf_headroom.h rf_sha256.h | $(HOST_OBJDIR)
	$(HOSTCC) $(HOST_CPPFLAGS) $(HOST_CFLAGS) $(DAEMON_SRC) test/fake_pl.c test/test_rf_headroom.c -o $@ -lm

$(HOST_OBJDIR)/headroom_owned_core.o: rf_core.c rf_core.h | $(HOST_OBJDIR)
	$(HOSTCC) $(HOST_CPPFLAGS) $(HOST_CFLAGS) -Drf_core_safe_shutdown=headroom_actual_safe_shutdown -c rf_core.c -o $@

$(HOST_OBJDIR)/test_rf_headroom_owned: $(DAEMON_SRC) test/test_rf_headroom_owned.c test/test_rf_headroom.c test/test_rf_daemon.c $(HOST_OBJDIR)/headroom_owned_core.o | $(HOST_OBJDIR)
	$(HOSTCC) $(HOST_CPPFLAGS) $(HOST_CFLAGS) $(filter-out rf_core.c,$(DAEMON_SRC)) test/fake_pl.c test/test_rf_headroom_owned.c $(HOST_OBJDIR)/headroom_owned_core.o -o $@ -lm
