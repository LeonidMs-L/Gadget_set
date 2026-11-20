import hashlib
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs


root = Path(SPECPATH)
model_hashes = {
    "decoder.onnx": "bd695497bf1b22279b7626f5916c79226e1e244c84355f8da7edfd2d921d0072",
    "encoder.onnx": "01bf5dc25539ca0cd5b1bd29296ea495977a6ba5f629dc4178277809d26e5e7d",
    "image_resizer.onnx": "e0b075c39700f64d50400f39c8fc186bbb3b5d84d31864008313f376603aca9d",
}
for name, expected in model_hashes.items():
    with (root / "models" / name).open("rb") as file:
        if hashlib.file_digest(file, "sha256").hexdigest() != expected:
            raise ValueError(f"Invalid OCR model: {name}")

data = [(str(root / "assets"), "assets"), (str(root / "models"), "models")]
for package in ("webview", "rapid_latex_ocr", "rapidocr_onnxruntime", "pypdfium2", "pypdfium2_raw"):
    data += collect_data_files(package)
for info in (Path(sys.prefix) / "Lib" / "site-packages").glob("*.dist-info"):
    for file in info.rglob("*"):
        if file.is_file() and file.name.lower().startswith(("license", "copying", "notice")):
            data.append((str(file), str(Path("licenses") / file.parent.relative_to(info.parent))))

analysis = Analysis(
    [str(root / "app.py")],
    datas=data,
    binaries=collect_dynamic_libs("webview") + collect_dynamic_libs("pypdfium2_raw"),
    hiddenimports=["webview.platforms.winforms", "webview.platforms.edgechromium", "pythonnet", "clr"],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "matplotlib", "IPython", "pytest"],
)
archive = PYZ(analysis.pure)
exe = EXE(
    archive, analysis.scripts, analysis.binaries, analysis.datas, [],
    name="FormulaDesk", console=False, icon=str(root / "assets" / "app.ico"), upx=False,
)
