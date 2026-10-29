"""Render the vector app icon to PNG and a multiresolution Windows ICO."""
from __future__ import annotations

import argparse
from pathlib import Path
import struct

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


SIZES = (16, 24, 32, 48, 64, 128, 256)


def png_frame(source: QImage, size: int) -> bytes:
    fitted = source.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
    canvas = QImage(size, size, QImage.Format.Format_ARGB32)
    canvas.fill(Qt.GlobalColor.transparent)
    x, y = (size - fitted.width()) // 2, (size - fitted.height()) // 2
    painter = QPainter(canvas)
    painter.drawImage(x, y, fitted)
    painter.end()

    data = QByteArray()
    buffer = QBuffer(data)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not canvas.save(buffer, "PNG"):
        raise RuntimeError(f"Could not encode {size}px PNG icon frame")
    buffer.close()
    return bytes(data)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", nargs="?", type=Path, default=Path("assets/fauxphonic-icon.svg"))
    parser.add_argument("output", nargs="?", type=Path, default=Path("assets/fauxphonic.ico"))
    args = parser.parse_args()
    if args.source.suffix.lower() == ".svg":
        renderer = QSvgRenderer(str(args.source))
        if not renderer.isValid() or renderer.defaultSize().isEmpty():
            raise SystemExit(f"Cannot read icon vector: {args.source}")
        if renderer.defaultSize().width() != renderer.defaultSize().height():
            raise SystemExit("Icon vector must be square before conversion.")
        image = QImage(1024, 1024, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        png = args.source.with_suffix(".png")
        if not image.save(str(png), "PNG"):
            raise SystemExit(f"Cannot write icon PNG: {png}")
        print(f"Wrote {png} at 1024 x 1024 px")
    else:
        image = QImage(str(args.source))
    if image.isNull():
        raise SystemExit(f"Cannot read icon image: {args.source}")
    if image.width() != image.height():
        raise SystemExit("Icon image must be square before icon conversion.")

    frames = [png_frame(image, size) for size in SIZES]
    header_size = 6 + 16 * len(frames)
    entries = bytearray()
    offset = header_size
    for size, frame in zip(SIZES, frames, strict=True):
        dimension = 0 if size == 256 else size
        entries.extend(struct.pack("<BBBBHHII", dimension, dimension, 0, 0,
                                   1, 32, len(frame), offset))
        offset += len(frame)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(struct.pack("<HHH", 0, 1, len(frames)) + entries + b"".join(frames))
    print(f"Wrote {args.output} with {len(frames)} transparent PNG frames: {', '.join(map(str, SIZES))} px")


if __name__ == "__main__":
    main()
