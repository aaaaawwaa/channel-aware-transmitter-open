#!/usr/bin/env python3
"""AC820 任意波形发生器上位机入口。"""
import sys

from PySide6.QtWidgets import QApplication

from siggen.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
