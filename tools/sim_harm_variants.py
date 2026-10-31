"""穷举 harm_synth 正弦表/地址实现变体,找出与硬件现象(偶次谐波反相180°/奇次同相0°、
频率精确、波形干净)匹配的变体。纯模拟,整数算术复现 RTL。
"""
import numpy as np

FWORD = 85900
N = 100_000               # 2 ms @ 50 MHz,采样=每拍
ORDS = [1, 2, 3, 4, 5]
M32 = np.uint64(0xFFFFFFFF)

t = np.arange(N, dtype=np.uint64)
acc0 = (t * np.uint64(FWORD)) & M32                      # 32-bit 累加器 wrap
phs = {k: acc0 * np.uint64(k) for k in ORDS}             # 36-bit 无符号乘积(<2^35 不截断)
addr = {k: ((phs[k] >> np.uint64(24)) & np.uint64(0xFF)).astype(np.int64) for k in ORDS}   # [31:24]
addr25 = {k: ((phs[k] >> np.uint64(25)) & np.uint64(0xFF)).astype(np.int64) for k in ORDS} # [32:25]

acc0s = acc0.astype(np.int64)
acc0s = np.where(acc0s >= 2**31, acc0s - 2**32, acc0s)   # acc0 有符号解释
phss = {k: acc0s * k for k in ORDS}
addrs = {k: ((phss[k] >> 24) & 0xFF).astype(np.int64) for k in ORDS}  # 有符号乘积取[31:24]

a = np.arange(256)
Tsin = np.round(127 * np.sin(2 * np.pi * a / 256)).astype(np.int64)
Tcos = np.round(127 * np.cos(2 * np.pi * a / 256)).astype(np.int64)
Tneg = -Tsin
Trect = Tsin.copy(); Trect[128:] = -Trect[128:]

def trdiv(x, d):
    return np.where(x >= 0, x // d, -((-x) // d))        # 整数除法向零截断(同 Verilog)

def gen(a0, a1, T0, T1, sign_h=1):
    s = 50 * T0[a0] + sign_h * 50 * T1[a1]
    return trdiv(s, 100).astype(np.float64)

# 最小二乘基: DC + sin/cos @ k*fword/2^32
w1 = 2 * np.pi * FWORD / 2**32
tt = t.astype(np.float64)
X = [np.ones(N)]
for k in ORDS:
    X += [np.cos(k * w1 * tt), np.sin(k * w1 * tt)]
X = np.column_stack(X)

def fit(sig):
    coef, *_ = np.linalg.lstsq(X, sig, rcond=None)
    r = sig - X @ coef
    nres = np.sqrt(np.mean(r**2)) / np.sqrt(np.mean(sig**2))
    # 正弦锚: s = A*sin(wt+phi) -> S=A*cos(phi), C=A*sin(phi) -> phi=atan2(C,S)
    phi = {k: float(np.degrees(np.arctan2(coef[2*i-1], coef[2*i]))) for i, k in enumerate(ORDS, 1)}
    amps = {k: float(np.hypot(coef[2*i-1], coef[2*i])) for i, k in enumerate(ORDS, 1)}
    ac = float(np.mean(sig**2) - np.mean(sig)**2)
    return phi, nres, amps, ac

def norm(x):
    x = x % 360.0
    if x > 180.0: x -= 360.0
    if x < -179.5: x += 360.0   # +-180 折为 180
    return x

VARIANTS = {
    "0_基线(正确)":      lambda k: gen(addr[1], addr[k], Tsin, Tsin),
    "1_表整体取反":      lambda k: gen(addr[1], addr[k], Tneg, Tneg),
    "2_地址^0x80":       lambda k: gen(addr[1] ^ 0x80, addr[k] ^ 0x80, Tsin, Tsin),
    "3_地址^0xFF":       lambda k: gen(addr[1] ^ 0xFF, addr[k] ^ 0xFF, Tsin, Tsin),
    "4_地址+128":        lambda k: gen((addr[1] + 128) & 0xFF, (addr[k] + 128) & 0xFF, Tsin, Tsin),
    "5_地址7bit截断":    lambda k: gen(addr[1] & 0x7F, addr[k] & 0x7F, Tsin, Tsin),
    "6_表上半取反":      lambda k: gen(addr[1], addr[k], Trect, Trect),
    "7a_谐波x(-1)^ord":  lambda k: gen(addr[1], addr[k], Tsin, Tsin, sign_h=(-1)**k),
    "7b_谐波x(-1)^(o+1)":lambda k: gen(addr[1], addr[k], Tsin, Tsin, sign_h=(-1)**(k+1)),
    "8_取[32:25]":       lambda k: gen(addr25[1], addr25[k], Tsin, Tsin),
    "9_有符号乘法":      lambda k: gen(addrs[1], addrs[k], Tsin, Tsin),
    "10_cos表":          lambda k: gen(addr[1], addr[k], Tcos, Tcos),
    "11_仅谐波读表取反": lambda k: gen(addr[1], addr[k], Tsin, Tneg),
    "12_仅谐波地址^80":  lambda k: gen(addr[1], addr[k] ^ 0x80, Tsin, Tsin),
}

print(f"{'变体':<18}{'phi1':>8}{'d2':>8}{'d3':>8}{'d4':>8}{'d5':>8}{'残差':>8}{'占比':>7}")
for name, fn in VARIANTS.items():
    row, res, phi1, share_min = [], 0.0, 0.0, 1.0
    for k in ORDS[1:]:
        phi, nres, amps, ac = fit(fn(k))
        row.append(norm(phi[k] - k * phi[1]))
        phi1, res = phi[1], max(res, nres)
        share = (amps[k]**2 / 2.0) / max(ac, 1e-30)   # 能量落在标称 k*f1 的占比
        share_min = min(share_min, share)
    print(f"{name:<18}{phi1:>8.1f}" + "".join(f"{d:>8.1f}" for d in row)
          + f"{res:>8.3f}{share_min:>7.2f}")
