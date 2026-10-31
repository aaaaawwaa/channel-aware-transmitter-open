from scripts import patch_vitis_linker


LINKER = """SECTIONS
{
.text : {
   KEEP (*(.vectors))
   *(.text)
} > ps7_ddr_0_memory_0
.rodata : { *(.rodata) } > ps7_ddr_0_memory_0
.bss (NOLOAD) : { *(.bss) } > ps7_ddr_0_memory_0
}
"""


CMAKE = """set(DDR ps7_ddr_0_memory_0)
set(CODE ps7_ddr_0_memory_0)
set(DATA ps7_ddr_0_memory_0)
"""


def test_patch_app_places_only_text_in_ocm(tmp_path):
    app = tmp_path / "awg_bridge"
    source = app / "src"
    source.mkdir(parents=True)
    linker = source / "lscript.ld"
    config = source / "Empty_applicationExample.cmake"
    linker.write_text(LINKER, encoding="utf-8")
    config.write_text(CMAKE, encoding="utf-8")

    assert patch_vitis_linker.patch_app(app) == {
        "config_changed": True,
        "linker_changed": True,
    }
    assert "set(CODE ps7_ram_0_memory_0)" in config.read_text(encoding="utf-8")
    output = linker.read_text(encoding="utf-8")
    assert ".text" in output
    assert ".text" in output and ".rodata" in output
    assert output.count("> ps7_ram_0_memory_0") == 1
    assert output.count("> ps7_ddr_0_memory_0") == 2


def test_patch_app_is_idempotent(tmp_path):
    app = tmp_path / "awg_bridge"
    source = app / "src"
    source.mkdir(parents=True)
    (source / "lscript.ld").write_text(LINKER.replace(
        "> ps7_ddr_0_memory_0", "> ps7_ram_0_memory_0", 1
    ), encoding="utf-8")
    (source / "Empty_applicationExample.cmake").write_text(
        CMAKE.replace("set(CODE ps7_ddr_0_memory_0)",
                      "set(CODE ps7_ram_0_memory_0)"), encoding="utf-8"
    )

    assert patch_vitis_linker.patch_app(app) == {
        "config_changed": False,
        "linker_changed": False,
    }
