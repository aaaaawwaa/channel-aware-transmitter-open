"""Boot Linux with clk_ignore_unused added to the otherwise identical
bootargs, from the current U-Boot prompt. The PL is left exactly as it is
(no JTAG reconfiguration, no reset), so the only difference from the
previous boots is the kernel parameter."""
import sys
import time

import serial

PORT = "COM11"
BAUD = 115200
BASE = ("console=ttyPS0,115200 earlyprintk root=/dev/mmcblk0p2 rw rootwait "
        "systemd.mask=pynq-status.service")


def main():
    extra = sys.argv[1] if len(sys.argv) > 1 else "clk_ignore_unused"
    dur = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0
    out_path = sys.argv[3] if len(sys.argv) > 3 else "serial_boot_testg.txt"
    ser = serial.Serial(PORT, BAUD, timeout=0.2)
    time.sleep(0.3)
    ser.reset_input_buffer()

    for _ in range(6):
        ser.write(b"\x03")
        time.sleep(0.4)
    ser.write(b"\r\n")
    time.sleep(0.5)
    ser.write(b"\r\n")
    time.sleep(0.5)

    ser.write(b"setenv bootargs '%s %s'\r\n" % (BASE.encode(), extra.encode()))
    time.sleep(0.5)
    ser.write(b"setenv scriptaddr 0x20000000\r\n")
    time.sleep(0.5)
    ser.write(b"run bootcmd_mmc0\r\n")

    buf = bytearray()
    t0 = time.time()
    with open(out_path, "wb") as f:
        while time.time() - t0 < dur:
            chunk = ser.read(4096)
            if chunk:
                buf += chunk
                f.write(chunk)
                f.flush()
    ser.close()
    sys.stdout.buffer.write(bytes(buf[-3000:]))
    print("\n=== captured %d bytes -> %s ===" % (len(buf), out_path))


if __name__ == "__main__":
    main()
