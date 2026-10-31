"""pytest 共享配置:Qt 控件测试统一 offscreen 平台(须在任何 PySide6 import 之前)。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
