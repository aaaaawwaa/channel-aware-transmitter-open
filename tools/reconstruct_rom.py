"""
Reconstruct the 256x8 sine ROM from Vivado synthesis netlist LUT INIT values.
Compares reconstructed table against the expected mathematical sin table.

Note: Bit 7 is special - the ROM output bit 7 is merged with the waveform
selection MUX in sample[7]_i_1 LUT6 (INIT=64'h775588AA775488BA).
For sine wave (wave_sel=00), we need to evaluate what this LUT outputs.
"""
import math

expected = [round(127 * math.sin(2 * math.pi * i / 256)) & 0xFF for i in range(256)]

class LUT6:
    def __init__(self, init_hex, inputs):
        self.init = int(init_hex, 16)
        self.inputs = inputs  # list of 6 bit indices (0-7) for I0..I5
    def eval(self, addr):
        lut_addr = 0
        for i, bit_idx in enumerate(self.inputs):
            if (addr >> bit_idx) & 1:
                lut_addr |= (1 << i)
        return (self.init >> lut_addr) & 1

# ===== Bit 0 =====
# MUXF7 S=p[5], I0=sample[0]_i_3 (p5=0), I1=sample[0]_i_4 (p5=1)
bit0_i3 = LUT6('0A5D0F3A6C50477E', [6, 4, 3, 0, 2, 1])
bit0_i4 = LUT6('EF5035BE20CA60AC', [6, 4, 1, 0, 2, 3])
def eval_bit0(addr):
    if (addr >> 5) & 1:
        return bit0_i4.eval(addr)
    else:
        return bit0_i3.eval(addr)

# ===== Bit 1 =====
# MUXF8 S=p[5], two MUXF7 S=p[6]
bit1_i5 = LUT6('55FCA5370A83C068', [4, 3, 7, 2, 0, 1])
bit1_i6 = LUT6('56655301B98FDDE9', [4, 3, 7, 2, 1, 0])
bit1_i7 = LUT6('26B90A8A3AED7F5D', [4, 7, 1, 2, 3, 0])
bit1_i8 = LUT6('1CCEF330055AAAB5', [4, 3, 7, 0, 1, 2])
def eval_bit1(addr):
    p5 = (addr >> 5) & 1
    p6 = (addr >> 6) & 1
    if p5 == 0:
        return bit1_i5.eval(addr) if p6 == 0 else bit1_i6.eval(addr)
    else:
        return bit1_i7.eval(addr) if p6 == 0 else bit1_i8.eval(addr)

# ===== Bit 2 =====
bit2_i5 = LUT6('277882B7788FF468', [4, 3, 7, 0, 1, 2])
bit2_i6 = LUT6('87D680A8E0A57D5F', [4, 0, 1, 2, 3, 7])
bit2_i7 = LUT6('4AF848B55DA71DA2', [4, 2, 3, 7, 0, 1])
bit2_i8 = LUT6('F2141FE1E14EDEB4', [4, 3, 7, 2, 0, 1])
def eval_bit2(addr):
    p5 = (addr >> 5) & 1
    p6 = (addr >> 6) & 1
    if p5 == 0:
        return bit2_i5.eval(addr) if p6 == 0 else bit2_i6.eval(addr)
    else:
        return bit2_i7.eval(addr) if p6 == 0 else bit2_i8.eval(addr)

# ===== Bit 3 =====
bit3_i5 = LUT6('BCB44B3CC6C33CA4', [4, 3, 7, 0, 2, 1])
bit3_i6 = LUT6('6268222297D7DDD5', [4, 2, 1, 0, 3, 7])
bit3_i7 = LUT6('5B5A5A1A0E2D2F2D', [4, 3, 7, 1, 0, 2])
bit3_i8 = LUT6('C3C32D3C36D2D369', [4, 3, 7, 0, 1, 2])
def eval_bit3(addr):
    p5 = (addr >> 5) & 1
    p6 = (addr >> 6) & 1
    if p5 == 0:
        return bit3_i5.eval(addr) if p6 == 0 else bit3_i6.eval(addr)
    else:
        return bit3_i7.eval(addr) if p6 == 0 else bit3_i8.eval(addr)

# ===== Bit 4 =====
bit4_i5 = LUT6('6969D2965B5A9686', [4, 3, 7, 0, 2, 1])
bit4_i6 = LUT6('57AA0000A155FFFF', [3, 0, 1, 2, 4, 7])
bit4_i7 = LUT6('1E1E1E5E4A4B4B4B', [4, 3, 7, 1, 0, 2])
bit4_i8 = LUT6('9696B4A5AD69694B', [4, 3, 7, 0, 1, 2])
def eval_bit4(addr):
    p5 = (addr >> 5) & 1
    p6 = (addr >> 6) & 1
    if p5 == 0:
        return bit4_i5.eval(addr) if p6 == 0 else bit4_i6.eval(addr)
    else:
        return bit4_i7.eval(addr) if p6 == 0 else bit4_i8.eval(addr)

# ===== Bit 5 =====
bit5_i5 = LUT6('BB9D99DC44626622', [4, 3, 0, 2, 1, 7])
bit5_i6 = LUT6('E00000003FFFFFFF', [0, 1, 2, 3, 4, 7])
bit5_i7 = LUT6('0F0F0F0F1F1E1E1E', [4, 3, 7, 1, 0, 2])
bit5_i8 = LUT6('AABFDD55554022AA', [4, 1, 0, 2, 3, 7])
def eval_bit5(addr):
    p5 = (addr >> 5) & 1
    p6 = (addr >> 6) & 1
    if p5 == 0:
        return bit5_i5.eval(addr) if p6 == 0 else bit5_i6.eval(addr)
    else:
        return bit5_i7.eval(addr) if p6 == 0 else bit5_i8.eval(addr)

# ===== Bit 6 =====
# LUT5 sample[6]_i_2: INIT=32'h8B3F8B0C
# Inputs: I0=sample[6]_i_3, I1=p[5], I2=p[7], I3=p[6], I4=sample[6]_i_4
bit6_i3 = LUT6('FFEAAAAA00155555', [4, 1, 0, 2, 3, 7])
bit6_i4 = LUT6('55777776AA888888', [4, 3, 0, 2, 1, 7])
def eval_bit6(addr):
    b3 = bit6_i3.eval(addr)
    b4 = bit6_i4.eval(addr)
    p5 = (addr >> 5) & 1
    p7 = (addr >> 7) & 1
    p6 = (addr >> 6) & 1
    # LUT5 addr: I4=bit6_i4, I3=p6, I2=p7, I1=p5, I0=bit6_i3
    lut5_addr = (b4 << 4) | (p6 << 3) | (p7 << 2) | (p5 << 1) | b3
    return (0x8B3F8B0C >> lut5_addr) & 1

# ===== Bit 7 =====
# sample[7]_i_2: partial bit 7 pre-computation
#   I0=p[4], I1=addr[3], I2=addr[0], I3=addr[2], I4=addr[1], I5=p[7]
# sample[7]_i_1: final output mux + bit 7 for sine
#   INIT=64'h775588AA775488BA
#   I0=wave_sel_r[1], I1=wave_sel_r[0], I2=sample[7]_i_2_out,
#   I3=p[6], I4=p[7], I5=p[5]
# For sine (wave_sel=00): evaluate sample[7]_i_1 with I1=I0=0

bit7_i2 = LUT6('FFFFFFFE00000000', [4, 3, 0, 2, 1, 7])

def eval_bit7(addr):
    """Evaluate bit 7 of the sine ROM, accounting for merged mux LUT."""
    # sample[7]_i_2 partial result
    b7_pre = bit7_i2.eval(addr)
    p5 = (addr >> 5) & 1
    p7 = (addr >> 7) & 1
    p6 = (addr >> 6) & 1
    # sample[7]_i_1 LUT6 inputs (for sine: I1=I0=0 = wave_sel=00)
    # I0=0, I1=0, I2=b7_pre, I3=p6, I4=p7, I5=p5
    lut_addr = (p5 << 5) | (p7 << 4) | (p6 << 3) | (b7_pre << 2) | (0 << 1) | (0 << 0)
    # INIT for sample[7]_i_1 = 64'h775588AA775488BA
    init = 0x775588AA775488BA
    # Need to handle as 64-bit properly
    return (init >> lut_addr) & 1

# ===== Reconstruct =====
print("=== ROM Reconstruction from Synthesis INIT Values ===\n")

reconstructed = []
for addr in range(256):
    b0 = eval_bit0(addr)
    b1 = eval_bit1(addr)
    b2 = eval_bit2(addr)
    b3 = eval_bit3(addr)
    b4 = eval_bit4(addr)
    b5 = eval_bit5(addr)
    b6 = eval_bit6(addr)
    b7 = eval_bit7(addr)
    val = (b7 << 7) | (b6 << 6) | (b5 << 5) | (b4 << 4) | (b3 << 3) | (b2 << 2) | (b1 << 1) | b0
    reconstructed.append(val)

def to_signed(v):
    return v if v < 128 else v - 256

mismatches = []
for i in range(256):
    if reconstructed[i] != expected[i]:
        mismatches.append(i)

print(f"Total mismatches: {len(mismatches)} out of 256")
if mismatches:
    for i in mismatches[:30]:
        se = to_signed(expected[i])
        sr = to_signed(reconstructed[i])
        print(f"  addr[{i:3d}]: synth=0x{reconstructed[i]:02X}({sr:+4d}) expected=0x{expected[i]:02X}({se:+4d}) err={sr-se:+d}")

print("\n=== Key sample points ===")
for a in [0, 1, 63, 64, 65, 127, 128, 129, 191, 192, 193, 255]:
    se = to_signed(expected[a])
    sr = to_signed(reconstructed[a])
    ok = "MATCH" if reconstructed[a] == expected[a] else f"MISMATCH(err={sr-se:+d})"
    print(f"  addr[{a:3d}]: synth=0x{reconstructed[a]:02X}({sr:+4d}) expected=0x{expected[a]:02X}({se:+4d})  {ok}")

print(f"\n=== Conclusion ===")
if len(mismatches) == 0:
    print("SYNTHESIZED ROM TABLE == EXPECTED SIN TABLE. Perfect match!")
else:
    print(f"SYNTHESIZED ROM TABLE HAS {len(mismatches)} MISMATCHES.")
