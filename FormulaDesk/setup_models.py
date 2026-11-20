"""Prepare the four local files required by RapidLaTeXOCR."""

import argparse
import hashlib
import os
import shutil
import sys
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import URLError
from urllib.request import Request, urlopen


MODEL_DIR = Path(__file__).resolve().parent / "models"
RELEASE_URL = "https://github.com/RapidAI/RapidLaTeXOCR/releases/download/v0.0.0"
MODEL_HASHES = {
    "decoder.onnx": "bd695497bf1b22279b7626f5916c79226e1e244c84355f8da7edfd2d921d0072",
    "encoder.onnx": "01bf5dc25539ca0cd5b1bd29296ea495977a6ba5f629dc4178277809d26e5e7d",
    "image_resizer.onnx": "e0b075c39700f64d50400f39c8fc186bbb3b5d84d31864008313f376603aca9d",
    "tokenizer.json": "1dc27b18d6a518d0d5ff3f4bb7bd98521fe80ad39e5b2a246d4109f1bb9d5019",
}


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download_model(name, output):
    request = Request(f"{RELEASE_URL}/{name}", headers={"User-Agent": "FormulaDesk-ModelSetup"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=45) as source:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            return
        except (URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
            output.seek(0)
            output.truncate()
            print(f"{name} 下载连接中断，正在重试…", flush=True)
            time.sleep(attempt + 1)


def prepare_file(target, expected_hash, source_dir):
    if target.is_file() and sha256(target) == expected_hash:
        print(f"已校验 {target.name}", flush=True)
        return

    operation = "导入" if source_dir else "下载"
    print(f"正在{operation} {target.name}…", flush=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            dir=target.parent, prefix=target.name + ".", suffix=".part", delete=False
        ) as output:
            temporary = Path(output.name)
            if source_dir:
                with (source_dir / target.name).open("rb") as source:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
            else:
                download_model(target.name, output)

        if sha256(temporary) != expected_hash:
            raise ValueError(f"{target.name} 的 SHA-256 不匹配。请检查来源后重试。")
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"已校验 {target.name}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="准备 FormulaDesk 公式识别模型。")
    parser.add_argument("--from-dir", type=Path, metavar="PATH", help="从已有模型目录导入")
    args = parser.parse_args()
    try:
        MODEL_DIR.mkdir(exist_ok=True)
        for name, digest in MODEL_HASHES.items():
            prepare_file(MODEL_DIR / name, digest, args.from_dir)
    except (OSError, ValueError) as error:
        print(f"模型准备失败。{error}", file=sys.stderr)
        return 1
    print(f"模型已就绪。{MODEL_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
