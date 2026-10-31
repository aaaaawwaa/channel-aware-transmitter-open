from pathlib import Path
import re
import sys


_OCM = "ps7_ram_0_memory_0"
_DDR = "ps7_ddr_0_memory_0"
_CODE_PATTERN = re.compile(
    r"set\(CODE\s+(?P<memory>[^)\s]+)\)", re.MULTILINE
)
_TEXT_PATTERN = re.compile(
    r"(?P<section>\.text\s*:\s*\{.*?\}\s*>\s*)(?P<memory>[^\s]+)",
    re.DOTALL,
)


def _patch_code_config(text):
    matches = list(_CODE_PATTERN.finditer(text))
    if len(matches) != 1:
        raise ValueError("Expected exactly one CODE setting")
    match = matches[0]
    if match.group("memory") == _OCM:
        return text, False
    if match.group("memory") != _DDR:
        raise ValueError("CODE must target DDR or OCM")
    start, end = match.span("memory")
    return text[:start] + _OCM + text[end:], True


def _patch_text_section(text):
    matches = list(_TEXT_PATTERN.finditer(text))
    if len(matches) != 1:
        raise ValueError("Expected exactly one .text output section")
    match = matches[0]
    memory = match.group("memory")
    if memory == _OCM:
        return text, False
    if memory != _DDR:
        raise ValueError(".text must target DDR or OCM")
    start, end = match.span("memory")
    return text[:start] + _OCM + text[end:], True


def patch_app(app):
    source = Path(app).absolute() / "src"
    config_path = source / "Empty_applicationExample.cmake"
    linker_path = source / "lscript.ld"
    config = config_path.read_text(encoding="utf-8")
    linker = linker_path.read_text(encoding="utf-8")
    config, config_changed = _patch_code_config(config)
    linker, linker_changed = _patch_text_section(linker)
    if config_changed:
        config_path.write_text(config, encoding="utf-8")
    if linker_changed:
        linker_path.write_text(linker, encoding="utf-8")
    return {"config_changed": config_changed, "linker_changed": linker_changed}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: patch_vitis_linker.py APP_DIR", file=sys.stderr)
        return 2
    try:
        print(patch_app(argv[0]))
    except (OSError, ValueError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
