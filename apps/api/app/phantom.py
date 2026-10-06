"""Synthetic chest "radiograph" phantoms.

These are drawn shapes, not real radiographs. They give the report generator
something to display and send through the pipeline without shipping any real
patient image.
"""

import struct
import zlib

import numpy as np


def _png(gray: np.ndarray) -> bytes:
    height, width = gray.shape
    raw = b"".join(b"\x00" + gray[row].tobytes() for row in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def chest_phantom(variant: str = "normal", size: int = 512, seed: int = 0) -> bytes:
    """variant: normal, nodule, effusion, cardiomegaly."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:size, 0:size] / size
    img = np.full((size, size), 0.55)

    def ellipse(cx, cy, rx, ry):
        return ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1

    # Lungs (dark), image-left is patient-right.
    right_lung = ellipse(0.33, 0.48, 0.15, 0.30)
    left_lung = ellipse(0.67, 0.48, 0.15, 0.30)
    img[right_lung | left_lung] = 0.18
    # Mediastinum and heart (bright).
    heart_rx = 0.17 if variant == "cardiomegaly" else 0.11
    img[ellipse(0.53, 0.62, heart_rx, 0.13)] = 0.72
    img[(np.abs(x - 0.5) < 0.05) & (y < 0.75)] = 0.75  # spine / mediastinum
    # Ribs.
    for i in range(8):
        cy = 0.24 + i * 0.07
        arc = np.abs((y - cy) - 0.08 * (x - 0.5) ** 2 * 4) < 0.008
        img[arc & (right_lung | left_lung)] = 0.42
    if variant == "nodule":
        img[ellipse(0.30, 0.32, 0.025, 0.025)] = 0.62
    if variant == "effusion":
        img[right_lung & (y > 0.64)] = 0.6
    # Film grain and soft edges.
    img += rng.normal(0, 0.03, img.shape)
    img = np.clip(img, 0, 1)
    return _png((img * 255).astype(np.uint8))
