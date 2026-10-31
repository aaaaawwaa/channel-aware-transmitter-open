# AutoDL Runner Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare an on-demand Vivado 2025.2 self-hosted runner. The target host is the Azure `fpga-runner` VM (Ubuntu 22.04, Standard B4s v2, 4 vCPU / 16 GiB); AutoDL is no longer the primary target.

## Installer findings (2026-09-24, Azure, verified)

The AMD Unified 2025.2 **Web Installer** is a ~347 MiB bootstrap, not an offline image. Its
`ConfigGen` action must be given explicit selectors: without them it prompts on stdin and, with
no console, re-reads EOF forever instead of failing.

- `xsetup -b ConfigGen` alone → logs `Selected Product is: Vitis`, then loops on
  `Not a valid choice. Please enter a number corresponding to the product you would like to
  install.` at roughly one line per millisecond until killed (~238 MB of log in 18 minutes).
  No config file is produced, and the process must be killed by hand.
- `xsetup -b ConfigGen -p Vivado` → same infinite loop, now on
  `Select an Edition from the list: 1. Vivado ML Standard  2. Vivado ML Enterprise`.
- `xsetup -b ConfigGen -p Vivado -e "Vivado ML Standard"` → **works**, exits 0, writes
  `~/.Xilinx/install_config.txt`.

Working headless install invocation (no console needed):

```bash
xsetup -a XilinxEULA,3rdPartyEULA -b Install \
  -c /home/<REMOTE_USER>/vivado-installer/install_config.txt
```

Config used: `Destination=/opt/Xilinx`, `Zynq-7000 All Programmable SoC:1`, every other device
family `:0`, `Vitis Embedded Development:0`, `DocNav:0`, shortcuts and file associations off.
Vivado Simulator (XSIM) ships with Vivado core and needs no separate selection. The installer
only offers whole device families — XC7Z020 cannot be picked on its own, it rides in Zynq-7000.

`AuthTokenGen` **requires a console**: with no TTY it aborts with
`FATAL Could not get a console!!!` / `Unable to progress with authentication token generation
without access to a console`. It must run over an interactive `ssh -tt` session with the AMD
account credentials typed by a human, never with `BatchMode`, output redirection, or `-T`.

After install, the vendor script `installLibs.sh` (root) must be run. `vivado -version`,
`xvlog -version`, `xelab -version`, and `xsim -version` were then confirmed live on the host.

**Architecture:** AutoDL keeps persistent tools, runner state, caches, and temporary files on `/root/autodl-tmp`; the no-card container is used only for installation and lightweight checks. The runner preflight measures cgroup limits rather than host-wide `/proc` values.

**Tech Stack:** Ubuntu 22.04 container, Bash, Python 3.11, GitHub Actions self-hosted runner, Vivado 2025.2, Vivado Simulator/XSim, `screen`, project `ci/check_vivado_runner.py`.

**Spec:** Conversation-approved two-stage AutoDL runner design from 2026-09-24.

## Global Constraints

- The Azure B4s v2 runner uses `CI_MIN_AVAILABLE_GB=12`, `CI_MIN_CPU=2`, and `HOG_NJOBS=1`; this is a low-resource best-effort profile, not evidence that Vivado cannot OOM.
- Do not alter the hardware acceptance gates: any bitstream still requires `GATE2-OK` and `BITSTREAM-OK` from one uninterrupted build.

---

### Task 1: Make runner resource validation cgroup-aware

**Files:**
- Modify: `ci/check_vivado_runner.py:13-54,77-83`
- Test: `host/tests/test_ci_runner.py`

**Interfaces:**
- Consumes: Linux cgroup v1/v2 files and existing `CI_MIN_AVAILABLE_GB` environment variable.
- Produces: memory and CPU values representing the container's effective limits; exits nonzero when the container cannot safely run the build.

- [x] **Step 1: Add tests for cgroup v1 and v2 parsing**

  Cover the current AutoDL files (`cpu.cfs_quota_us=50000`, `cpu.cfs_period_us=100000`, `memory.limit_in_bytes=2147483648`) and a v2 `cpu.max`/`memory.max` fixture. Assert that host-wide memory is never used when a finite cgroup limit exists.

- [x] **Step 2: Run the focused tests and confirm they fail**

  Run: `python -m pytest host/tests/test_ci_runner.py -q`

- [x] **Step 3: Implement minimal cgroup-aware resource helpers**

  Read cgroup v1 first when present, otherwise v2; cap `/proc/meminfo` availability by the effective cgroup memory limit and expose a minimum CPU threshold controlled by `CI_MIN_CPU`.

- [x] **Step 4: Run focused and existing host tests**

  Run: `python -m pytest host/tests/test_ci_runner.py host/tests -q`

- [x] **Step 5: Verify the current no-card container is rejected**

  Run the checker with `VIVADO_BIN` set to a real 2025.2 path after installation; before Vivado exists, verify the checker reports the missing executable rather than attempting a build.

---

### Task 2: Prepare the AutoDL persistent data-disk layout

**Files:**
- Remote create: `/root/autodl-tmp/xilinx/Vivado/2025.2`
- Remote create: `/root/autodl-tmp/actions-runner`
- Remote create: `/root/autodl-tmp/gha-work`
- Remote create: `/root/autodl-tmp/vivado-tmp`
- Remote create: `/root/autodl-tmp/project-cache`

**Interfaces:**
- Consumes: `/dev/md0` mounted at `/root/autodl-tmp`.
- Produces: persistent directories with writable permissions for root-owned initial setup and later runner execution.

- [x] **Step 1: Check free space and directory ownership**

  Run remotely: `df -h / /root/autodl-tmp && df -ih /root/autodl-tmp && ls -ld /root/autodl-tmp`.

- [x] **Step 2: Create the layout and a disk report**

  Create the five directories under `/root/autodl-tmp`, then write only non-secret path and capacity information to `/root/autodl-tmp/runner-layout.txt`.

- [x] **Step 3: Configure temporary paths for future jobs**

  Prepare a data-disk environment fragment exporting `TMPDIR`, `TEMP`, `TMP`, and `XILINX_LOCAL_USER_DATA` to data-disk locations; do not activate it globally until the runner starts.

- [x] **Step 4: Verify persistence and write access**

  Create and remove a harmless probe file under each directory and report `df -h` afterward.

---

### Task 3: Install lightweight no-card prerequisites

**Files:**
- Remote package state only; no repository file changes.

**Interfaces:**
- Consumes: Ubuntu 22.04 apt repositories and the persistent data-disk layout.
- Produces: Python 3.11-capable environment, GitHub runner prerequisites, and tools for lightweight XSim/Vivado setup checks.

- [x] **Step 1: Refresh package metadata without installing Vivado**

  Run `apt-get update` remotely and stop if repository access or package locks fail.

- [x] **Step 2: Install only lightweight prerequisites**

  Install `python3`, `python3-pip`, `python3-venv`, `ca-certificates`, `git`, `curl`, `wget`, `tar`, `unzip`, `screen`, `make`, `gcc`, and `libstdc++6`; do not install GPU/CUDA packages or Vitis.

- [x] **Step 3: Create a data-disk Python environment**

  Create `/root/autodl-tmp/project-cache/venv` and install the minimal packages needed for CI helper checks. Do not install the full GUI host dependency set unless the runner will execute host tests locally.

- [x] **Step 4: Verify versions and disk usage**

  Check Python, pip, Git, screen, compiler, and available data-disk capacity. Do not run the Vivado resource gate in no-card mode as a passing check.

---

### Task 4: Stage Vivado/XSIM and on-demand runner startup

**Files:**
- Remote create: `/home/<REMOTE_USER>/actions-runner/`
- Remote install root: `/opt/Xilinx/2025.2`
- Modify: `.github/workflows/awg-bitstream.yml:40-48` only if the selected runner startup contract requires a new environment variable.

**Interfaces:**
- Consumes: user-provided Vivado 2025.2 installer, AMD account credentials typed interactively for `AuthTokenGen`, and a GitHub runner registration token supplied interactively by the user.
- Produces: a runner label `vivado-2025.2`, a systemd unit (or explicit foreground launch), and `VIVADO_BIN` pointing at `/opt/Xilinx/2025.2/Vivado/bin/vivado`.

- [x] **Step 1: Confirm installer and license availability before transfer**

  The AMD Unified 2025.2 Web Installer bootstrap was uploaded to the Azure VM and its archive integrity verified. No license URL is guessed or logged; the CI workflow reads the license only from the `HOG_XIL_LICENSE` secret.

- [x] **Step 2: Install only Zynq-7000 and Vivado Simulator components**

  Installed with the vendor batch installer into `/opt/Xilinx/2025.2` using the limited
  `install_config.txt`. Confirmed present and runnable on the host: `vivado` (v2025.2 64-bit),
  `xvlog`, `xelab`, `xsim`, `xvhdl`. `installLibs.sh` was run as root afterwards. Synthesis was
  not started during installation.

- [ ] **Step 3: Configure the GitHub runner without embedding credentials**

  Use GitHub's current one-time registration token interactively, register labels `self-hosted,linux,x64,vivado-2025.2`, and keep runner work under `/root/autodl-tmp/gha-work`.

- [ ] **Step 4: Write a screen launcher**

  The launcher must export `VIVADO_BIN`, `TMPDIR`, and `CI_MIN_AVAILABLE_GB=20`, refuse to start when cgroup memory or CPU is below the threshold, and execute the official runner `run.sh` in a named screen session. It must not use systemd.

- [ ] **Step 5: Verify startup without accepting a build**

  Start the runner only in a test mode, confirm it appears online with the expected label, then stop it cleanly before switching to card mode.

---

### Task 5: Add XSIM CI coverage after resource mode is available

**Files:**
- Create: `scripts/run_xsim_ci.py` or an equivalent checked-in deterministic XSIM runner.
- Modify: `.github/workflows/awg-bitstream.yml` to add an XSIM job on `vivado-2025.2`.
- Test: focused simulation command and CI dry-run checks.

**Interfaces:**
- Consumes: `src/rtl/*.v`, `src/tb/*.v`, Vivado 2025.2 `xvlog/xelab/xsim`.
- Produces: machine-readable PASS/FAIL output and uploaded simulation logs.

- [ ] **Step 1: Select the existing deterministic baseline testbenches**

  Start with the module and top-level benches already documented in `CLAUDE.md`, including `reg_bridge_tb`, `awg_top_tb`, and the newer channel/IQ benches; do not silently mark hardware-only tests as passed.

- [ ] **Step 2: Implement one reproducible XSIM invocation per testbench**

  Use `--sv` where required, isolate generated `xsim.dir` output under the runner workspace, run `xsim -runall`, and return nonzero on any `FAIL` or missing expected `PASS` result.

- [ ] **Step 3: Run the selected suite in card mode**

  Record test count, elapsed time, Vivado version, and cgroup resource values. Keep the no-card result as `NOT RUN` for full regression.

- [ ] **Step 4: Add CI artifact collection**

  Upload XSIM logs and summaries on failure or success without uploading credentials or license files.

---

## Verification Checklist

- [ ] No-card cgroup is rejected by the resource gate.
- [ ] Data-disk directories survive container restart/recreation according to AutoDL's persistence behavior.
- [ ] Vivado 2025.2 executable and XSIM tools resolve from the data disk.
- [ ] Runner appears with label `vivado-2025.2` only when deliberately started.
- [ ] Host tests remain on GitHub-hosted Ubuntu.
- [ ] XSIM and bitstream jobs run only on a runner with sufficient cgroup resources.
- [ ] Bitstream acceptance requires `GATE2-OK`, `BITSTREAM-OK`, and manifest SHA-256 verification.
