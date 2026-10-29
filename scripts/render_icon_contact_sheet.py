"""Render embedded ICO frames at native size on light and dark backgrounds."""
from pathlib import Path
import struct

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter


def main() -> None:
    QGuiApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL)
    app = QGuiApplication([])
    icon_path = Path("assets/fauxphonic.ico")
    data = icon_path.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    if (reserved, kind) != (0, 1):
        raise SystemExit("Input is not a Windows icon file")
    sizes = []
    frames = []
    for index in range(count):
        width, height, _, _, _, _, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + index * 16)
        size = width or 256
        if (height or 256) != size:
            raise SystemExit("Icon frame is not square")
        sizes.append(size)
        frames.append(QImage.fromData(QByteArray(data[offset:offset + length]), "PNG"))

    selected = list(zip(sizes, frames, strict=True))
    cell_w, cell_h = 300, 330
    sheet = QImage(cell_w * len(selected), cell_h * 2, QImage.Format.Format_ARGB32)
    sheet.fill(Qt.GlobalColor.white)
    painter = QPainter(sheet)
    painter.setFont(QFont("Segoe UI", 11))
    for row, (background, foreground) in enumerate((("#f4f4f4", "#1a1a1a"), ("#20232b", "#f3f3f3"))):
        for column, (size, frame) in enumerate(selected):
            x, y = column * cell_w, row * cell_h
            painter.fillRect(x, y, cell_w - 1, cell_h - 1, QColor(background))
            painter.setPen(QColor(foreground))
            painter.drawText(x + 10, y + 25, f"{size} × {size} px")
            painter.drawImage(x + (cell_w - size) // 2, y + (cell_h - size) // 2, frame)
            painter.drawText(x + 10, y + 315, "Light background" if row == 0 else "Dark background")
    painter.end()
    output = Path("evidence/brand-icons.png")
    output.parent.mkdir(parents=True, exist_ok=True)
    if not sheet.save(str(output), "PNG"):
        raise SystemExit(f"Could not write {output}")
    print(output.resolve())
    app.quit()


if __name__ == "__main__":
    main()
