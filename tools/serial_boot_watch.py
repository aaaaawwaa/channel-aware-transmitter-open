"""Watch COM11 for the U-Boot banner after a PS reset, then drive the
known-good manual boot sequence (Ctrl-C out of the BOOTP loop, setenv
bootargs + scriptaddr, run bootcmd_mmc0). Passive until the trigger
appears, so it can be started BEFORE the reset is issued.

Usage: python serial_boot_watch.py <duration_sec> <outfile>"""
import sys
import time

import serial

PORT = "COM11"
BAUD = 115200
TRIGGERS = [b"Hit any key", b"BOOTP", b"Zynq>"]


def main():
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 240.0
    out_path = sys.argv[2] if len(sys.argv) > 2 else "serial_watch.txt"

    ser = serial.Serial(PORT, BAUD, timeout=0.2)
    time.sleep(0.3)
    ser.reset_input_buffer()

    buf = bytearray()
    t0 = time.time()
    driven = False
    with open(out_path, "wb") as f:
        while time.time() - t0 < dur:
            chunk = ser.read(4096)
            if chunk:
                buf += chunk
                f.write(chunk)
                f.flush()
                tail = bytes(buf[-400:])
                if not driven and any(t in tail for t in TRIGGERS):
                    time.sleep(1.5)
                    for _ in range(4):
                        ser.write(b"\x03")
                        time.sleep(0.4)
                    ser.write(b"\r\n")
                    time.sleep(0.6)
                    ser.write(b"setenv bootargs 'console=ttyPS0,115200 earlyprintk root=/dev/mmcblk0p2 rw rootwait systemd.mask=pynq-status.service'\r\n")
                    time.sleep(0.7)
                    ser.write(b"setenv scriptaddr 0x20000000\r\n")
                    time.sleep(0.7)
                    ser.write(b"run bootcmd_mmc0\r\n")
                    driven = True
                    f.write(b"\n<<< serial_boot_watch: drove U-Boot >>>\n")
    ser.close()
    sys.stdout.buffer.write(bytes(buf[-3000:]))
    print("\n=== %d bytes, driven=%s -> %s ===" % (len(buf), driven, out_path))


if __name__ == "__main__":
    main()
