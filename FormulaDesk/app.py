import base64
import io
import logging
import os
import time
from contextlib import closing
from functools import wraps
from pathlib import Path

import webview
import win32clipboard as clipboard
import win32con
import win32gui
from PIL import Image, ImageFilter, ImageGrab, ImageOps


ROOT = Path(__file__).resolve().parent
TITLE = "公式拾取 · Formula Desk"
MAX_FILE = 50 * 1024 * 1024
MAX_PIXELS = 40_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class InputError(Exception):
    pass


def bridge(method):
    @wraps(method)
    def call(self, *args):
        try:
            return {"ok": True, "data": method(self, *args)}
        except InputError as error:
            return {"ok": False, "error": str(error)}
        except Exception:
            logging.exception("Operation failed: %s", method.__name__)
            return {"ok": False, "error": "操作失败。请检查文件是否可打开，或重试。"}

    return call


def rgb_image(image):
    if image.width * image.height > MAX_PIXELS:
        raise InputError("图片像素过多。请缩小图片或裁出公式后重试。")
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, "white")
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    return image.convert("RGB")


def encode_image(image, format="PNG"):
    stream = io.BytesIO()
    image.save(stream, format, quality=92)
    return stream.getvalue()


def clipboard_write(formats):
    for attempt in range(8):
        try:
            clipboard.OpenClipboard(win32gui.FindWindow(None, TITLE))
            break
        except Exception:
            if attempt == 7:
                raise InputError("剪贴板正被占用，请稍后重试。") from None
            time.sleep(0.05)
    try:
        clipboard.EmptyClipboard()
        for kind, payload in formats:
            clipboard.SetClipboardData(kind, payload)
    finally:
        clipboard.CloseClipboard()


class Desk:
    def __init__(self):
        self._window = None
        self._image = None
        self._pdf = None
        self._page = 0
        self._name = ""
        self._upload = None
        self._formula_engine = None
        self._text_engine = None

    @bridge
    def choose(self):
        matches = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            file_types=("图片及 PDF (*.png;*.jpg;*.jpeg;*.bmp;*.webp;*.tif;*.tiff;*.pdf)", "所有文件 (*.*)"),
        )
        return self._open_path(Path(matches[0])) if matches else None

    @bridge
    def paste(self):
        item = ImageGrab.grabclipboard()
        if isinstance(item, Image.Image):
            return self._load_image(rgb_image(item), "剪贴板图片")
        if isinstance(item, list) and item:
            return self._open_path(Path(item[0]))
        raise InputError("剪贴板里没有图片或文件。可以先用 Win + Shift + S 截图。")

    @bridge
    def upload_start(self, name, size):
        if not 0 < size <= MAX_FILE:
            raise InputError("请选择 50 MB 以内的图片或 PDF。")
        self._upload = (str(name)[:250], size, bytearray())

    @bridge
    def upload_part(self, content):
        name, size, buffer = self._upload
        part = base64.b64decode(content, validate=True)
        if len(buffer) + len(part) > size:
            self._upload = None
            raise InputError("文件数据不完整，请重新导入。")
        buffer.extend(part)

    @bridge
    def upload_finish(self):
        name, size, buffer = self._upload
        self._upload = None
        if len(buffer) != size:
            raise InputError("文件数据不完整，请重新导入。")
        return self._open_data(bytes(buffer), name)

    @bridge
    def image_data(self, payload, name):
        return self._open_data(base64.b64decode(payload, validate=True), name)

    def _open_path(self, path):
        if not path.is_file():
            raise InputError("找不到所选文件。")
        if path.stat().st_size > MAX_FILE:
            raise InputError("请选择 50 MB 以内的图片或 PDF。")
        return self._open_data(path.read_bytes(), path.name)

    def _open_data(self, data, name):
        if b"%PDF-" in data[:1024] or name.lower().endswith(".pdf"):
            import pypdfium2 as pdfium

            try:
                pdf = pdfium.PdfDocument(data)
            except pdfium.PdfiumError as error:
                raise InputError("PDF 无法打开。请检查文件是否损坏或受密码保护。") from error
            try:
                if len(pdf) < 1:
                    raise InputError("这个 PDF 没有可识别的页面。")
                image = self._rasterize(pdf, 0)
            except Exception:
                pdf.close()
                raise
            self._replace(image, name, pdf)
        else:
            try:
                with Image.open(io.BytesIO(data)) as source:
                    image = rgb_image(source)
            except (OSError, Image.DecompressionBombError) as error:
                raise InputError("无法读取图片。请尝试 PNG、JPG、BMP、WebP 或 TIFF。") from error
            self._replace(image, name, None)
        return self._preview()

    def _replace(self, image, name, pdf):
        if self._pdf is not None:
            self._pdf.close()
        self._image, self._name, self._pdf, self._page = image, name, pdf, 0

    def _load_image(self, image, name):
        self._replace(image, name, None)
        return self._preview()

    @staticmethod
    def _rasterize(pdf, index):
        with closing(pdf[index]) as page:
            width, height = page.get_size()
            if min(width, height) <= 0:
                raise InputError("PDF 页面尺寸无效。")
            scale = min(3.0, 2800 / max(width, height))
            with closing(page.render(scale=scale, fill_color=(255, 255, 255, 255))) as bitmap:
                return bitmap.to_pil().convert("RGB")

    def _preview(self):
        preview = self._image.copy()
        preview.thumbnail((1400, 1400))
        encoded = base64.b64encode(encode_image(preview, "JPEG")).decode("ascii")
        return {"src": f"data:image/jpeg;base64,{encoded}", "name": self._name,
                "width": self._image.width, "height": self._image.height,
                "page": self._page + 1, "pages": len(self._pdf) if self._pdf is not None else 0}

    @bridge
    def page(self, index):
        if self._pdf is None or not 1 <= index <= len(self._pdf):
            raise InputError("PDF 页码超出范围。")
        self._image = self._rasterize(self._pdf, index - 1)
        self._page = index - 1
        return self._preview()

    @bridge
    def recognize(self, mode, crop, enhance=False):
        if self._image is None:
            raise InputError("请先导入图片、打开 PDF 或在手写板上书写。")
        image = self._image
        if crop:
            left, top, right, bottom = crop
            coordinates = (
                round(left * image.width), round(top * image.height),
                round(right * image.width), round(bottom * image.height),
            )
            if coordinates[2] - coordinates[0] < 5 or coordinates[3] - coordinates[1] < 5:
                raise InputError("选区太小，请重新框选。")
            image = image.crop(coordinates)
        return self._run_ocr(mode, image, bool(crop), enhance)

    @bridge
    def recognize_drawing(self, payload):
        data = base64.b64decode(payload, validate=True)
        with Image.open(io.BytesIO(data)) as image:
            return self._run_ocr("formula", rgb_image(image), True)

    def _run_ocr(self, mode, image, cropped, enhance=False):
        start = time.perf_counter()
        if mode == "formula":
            result = self._recognize_formula(image, enhance)
        elif mode == "document":
            result = self._recognize_text(image, cropped)
        else:
            raise InputError("识别模式无效。")
        if not result.strip():
            raise InputError("没有识别到内容。请检查图片清晰度和选区后重试。")
        return {"text": result.strip(), "seconds": round(time.perf_counter() - start, 1)}

    def _recognize_formula(self, image, enhance=False):
        gray = ImageOps.autocontrast(image.convert("L"), cutoff=0.2)
        edges = (gray.getpixel((0, 0)), gray.getpixel((gray.width - 1, 0)),
                 gray.getpixel((0, gray.height - 1)), gray.getpixel((gray.width - 1, gray.height - 1)))
        if sum(edges) / len(edges) < 110:
            gray = ImageOps.invert(gray)
        if enhance:
            sharp = gray.filter(ImageFilter.UnsharpMask(radius=2, percent=170, threshold=2))
            gray = sharp.point(lambda value: 0 if value < 180 else 255)
        ink = gray.point(lambda value: 255 if value < 190 else 0)
        bounds = ink.getbbox()
        if not bounds:
            raise InputError("图片中没有找到清晰的公式笔画。")
        gray = gray.crop(bounds)
        if gray.height < 28:
            ratio = 28 / gray.height
            gray = gray.resize((max(1, round(gray.width * ratio)), 28), Image.Resampling.LANCZOS)
        gray = ImageOps.expand(gray, border=5, fill=255)
        if self._formula_engine is None:
            from rapid_latex_ocr import LaTeXOCR

            models = ROOT / "models"
            paths = {key: models / name for key, name in {
                "image_resizer_path": "image_resizer.onnx", "encoder_path": "encoder.onnx",
                "decoder_path": "decoder.onnx", "tokenizer_json": "tokenizer.json",
            }.items()}
            if any(not path.is_file() for path in paths.values()):
                raise InputError("缺少公式模型。请重新获取完整的程序。")
            self._formula_engine = LaTeXOCR(**paths)
        text, _ = self._formula_engine(encode_image(gray.convert("RGB")))
        return text

    def _recognize_text(self, image, cropped):
        if self._pdf is not None and not cropped:
            with closing(self._pdf[self._page]) as page, closing(page.get_textpage()) as text_page:
                text = text_page.get_text_bounded().strip()
            if text:
                return text.replace("\r\n", "\n")
        from rapidocr_onnxruntime import RapidOCR

        if self._text_engine is None:
            self._text_engine = RapidOCR(intra_op_num_threads=4, inter_op_num_threads=1)
        lines, _ = self._text_engine(encode_image(image))
        return "\n".join(line[1] for line in lines or [])

    @bridge
    def copy_text(self, value, mathml=False):
        if not value:
            raise InputError("当前没有可复制的内容。")
        formats = [(win32con.CF_UNICODETEXT, value.replace("\n", "\r\n"))]
        if mathml:
            xml = value.encode("utf-8") + b"\0"
            formats.append((clipboard.RegisterClipboardFormat("MathML"), xml))
            formats.append((clipboard.RegisterClipboardFormat("MathML Presentation"), xml))
        clipboard_write(formats)

    @bridge
    def copy_png(self, payload):
        png = base64.b64decode(payload, validate=True)
        with Image.open(io.BytesIO(png)) as image:
            dib = encode_image(image.convert("RGB"), "BMP")[14:]
        clipboard_write([(clipboard.RegisterClipboardFormat("PNG"), png), (win32con.CF_DIB, dib)])

    @bridge
    def save(self, kind, payload):
        extensions = {"png": ("PNG 图像 (*.png)", "png"), "latex": ("LaTeX 文件 (*.tex)", "tex"),
                      "markdown": ("Markdown 文件 (*.md)", "md"), "mathml": ("MathML 文件 (*.mml)", "mml")}
        file_type, extension = extensions[kind]
        path = self._window.create_file_dialog(
            webview.FileDialog.SAVE, save_filename=f"formula.{extension}", file_types=(file_type,)
        )
        if not path:
            return False
        filename = Path(path[0] if isinstance(path, (tuple, list)) else path)
        if not filename.suffix:
            filename = filename.with_suffix(f".{extension}")
        if kind == "png":
            filename.write_bytes(base64.b64decode(payload, validate=True))
        else:
            filename.write_text(payload, encoding="utf-8")
        return True


def main():
    log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "FormulaDesk"
    log_dir.mkdir(parents=True, exist_ok=True)
    from logging.handlers import RotatingFileHandler

    handler = RotatingFileHandler(log_dir / "app.log", maxBytes=500_000, backupCount=1, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    app = Desk()
    app._window = webview.create_window(
        TITLE, str(ROOT / "assets" / "index.html"), js_api=app,
        width=1320, height=820, min_size=(790, 640), background_color="#101a2b",
    )
    try:
        webview.start(gui="edgechromium", private_mode=True, icon=str(ROOT / "assets" / "app.ico"))
    except Exception:
        logging.exception("Window initialization failed")
        win32gui.MessageBox(0, "程序窗口无法启动。请确认系统已安装 Microsoft Edge WebView2 运行时。", TITLE, 0x10)


if __name__ == "__main__":
    main()
