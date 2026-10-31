#!/usr/bin/env bash
# 下载 bitstream 到板卡(下载前自动校验,防旧版本)。
# 用法: bash scripts/program_board.sh
set -e
cd "$(cd "$(dirname "$0")" && pwd)/.."

if ! python scripts/check_bitstream.py; then
  echo "!! bitstream 过期, 请先构建: python scripts/build_bitstream.py"
  exit 1
fi

program_output="$(./vivado2025.cmd --json hw program --bit awg/awg.runs/impl_1/host_bd_wrapper.bit)"
printf '%s\n' "$program_output"
if ! PROGRAM_OUTPUT="$program_output" python -c 'import json, os, sys; result = json.loads(os.environ["PROGRAM_OUTPUT"]); raise SystemExit(0 if result.get("ok") else 1)'; then
  echo "!! JTAG 下载失败, 未确认 bitstream 已写入板卡" >&2
  exit 1
fi
echo "== bitstream 已下载。若桥程序未运行, 用 vivado2025.cmd deploy jtag 启动 =="
