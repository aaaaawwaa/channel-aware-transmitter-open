"""Serial transport layer: background read thread + main-thread writes.
Disconnection/error reported via signals.
"""
from typing import Optional

import serial
from PySide6.QtCore import QThread, Signal


class SerialWorker(QThread):
    line_received = Signal(str)
    connection_lost = Signal(str)

    def __init__(self, port: str, baud: int = 115200, parent=None):
        super().__init__(parent)
        self._port = port
        self._baud = baud
        self._ser = None
        self._quit = False

    def open(self) -> Optional[str]:
        """Open the serial port; returns error message on failure, None on success."""
        try:
            self._ser = serial.Serial(self._port, self._baud, timeout=0.2)
        except serial.SerialException as e:
            return str(e)
        return None

    def send(self, text: str) -> None:
        if self._ser is None:
            return
        try:
            self._ser.write(text.encode())
        except serial.SerialException as e:
            self.connection_lost.emit(str(e))
            self._close()

    def stop(self) -> None:
        self._quit = True
        self._close()
        self.wait(1000)

    def _close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except serial.SerialException:
                pass
            self._ser = None

    def run(self) -> None:
        while not self._quit:
            if self._ser is None:
                self.msleep(100)
                continue
            try:
                line = self._ser.readline()
            except serial.SerialException as e:
                self.connection_lost.emit(str(e))
                self._close()
                break
            if line:
                self.line_received.emit(line.decode(errors="replace").strip())
