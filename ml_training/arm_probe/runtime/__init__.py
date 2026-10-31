"""Standalone runtime modules for the frozen round-2 replay probe.

Only NumPy (plus the Python standard library) is required.  The modules are a frozen copy of
the round-1/round-2 offline inference path with package-relative imports; the files are
byte-identical in behaviour to ``ml_training.model.numpy_runtime``,
``ml_training.model.baseline``, ``ml_training.common.preprocess/gate/iq``,
``ml_training.model.features`` and ``ml_training.round2.features``.

Python target for the board: CPython 3.8.x / NumPy 1.17.4 (ARMv7l) -- see COMPATIBILITY.md.
"""
