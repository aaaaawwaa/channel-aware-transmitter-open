"""Drive the AC820 serial console (COM11) through an interactive session.

Sends the stable-diagnostic boot sequence used throughout this investigation:
  Ctrl-C x3  -> interrupt the U-Boot BOOTP loop
  <Enter>     -> confirm the `Zynq>` prompt
  setenv bootargs 'console=ttyPS0,115200 earlyprintk root=/dev/mmcblk0p2 rw rootwait systemd.mask=pynq-status.service'
  setenv scriptaddr 0x20000000
  run bootcmd_mmc0

Then tails the console for the specified duration so the full Linux boot
lands in the capture file. UTF-8 safe (a previous session lost the reader
thread to a GBK UnicodeEncodeError on '\xa9')."""
import sys
import time

import serial

PORT = "COM11"
BAUD = 115200


def main():
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0
    out_path = sys.argv[2] if len(sys.argv) > 2 else "serial_drive_capture.txt"
    ser = serial.Serial(PORT, BAUD, timeout=0.2)
    time.sleep(0.3)
    ser.reset_input_buffer()

    # Interrupt the BOOTP loop (may need several Ctrl-C; U-Boot is mid-delay)
    for _ in range(6):
        ser.write(b"\x03")
        time.sleep(0.4)
    ser.write(b"\r\n")
    time.sleep(0.5)
    ser.write(b"\r\n")
    time.sleep(0.5)

    ser.write(b"setenv bootargs 'console=ttyPS0,115200 earlyprintk root=/dev/mmcblk0p2 rw rootwait systemd.mask=pynq-status.service'\r\n")
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
