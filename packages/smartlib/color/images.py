"""Float EXR fixtures and finite, per-channel image comparisons."""
from __future__ import annotations

import math


def chart():
    import numpy as np
    # Negative, near-black, middle grey, white, HDR and saturated ACEScg values.
    values = [-0.01, 0.0, 0.001, 0.01, 0.18, 0.5, 1.0, 2.0, 4.0, 16.0]
    pixels = np.zeros((128, 320, 3), dtype=np.float32)
    for i, value in enumerate(values):
        pixels[:64, i * 32:(i + 1) * 32] = value
    colors = [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0), (0, 1, 1),
              (1, 0, 1), (4, 0.18, 0.01), (0.18, 4, 0.01), (0.01, 0.18, 4), (0.18, 0.18, 0.18)]
    for i, color in enumerate(colors):
        pixels[64:, i * 32:(i + 1) * 32] = color
    return pixels


def write_exr(path, pixels, space):
    import OpenImageIO as oiio
    spec = oiio.ImageSpec(pixels.shape[1], pixels.shape[0], pixels.shape[2], oiio.FLOAT)
    spec.attribute("oiio:ColorSpace", space)
    spec.attribute("compression", "zip")
    output = oiio.ImageOutput.create(str(path))
    if not output or not output.open(str(path), spec):
        raise RuntimeError(f"Cannot write EXR: {path}")
    try:
        if not output.write_image(pixels):
            raise RuntimeError(output.geterror())
    finally:
        output.close()


def read_exr(path):
    import OpenImageIO as oiio
    import numpy as np
    source = oiio.ImageInput.open(str(path))
    if not source:
        raise ValueError(f"Unreadable image: {path}")
    try:
        if source.format_name() != "openexr":
            raise ValueError(f"Expected OpenEXR: {path}")
        spec = source.spec()
        formats = list(spec.channelformats) or [spec.format] * spec.nchannels
        names = list(spec.channelnames)
        indices = [names.index(c) for c in ("R", "G", "B")]
        if any(formats[i] != oiio.FLOAT for i in indices):
            raise ValueError(f"Expected 32-bit float RGB: {path}")
        if spec.x or spec.y or spec.width != spec.full_width or spec.height != spec.full_height:
            raise ValueError(f"Non-matching data/display window: {path}")
        data = source.read_image(format=oiio.FLOAT)
        if data is None:
            raise ValueError(f"Image decode failed: {path}")
        return np.asarray(data)[:, :, indices]
    finally:
        source.close()


def compare(reference, actual, *, atol=1e-5, rtol=1e-5):
    import numpy as np
    for value in (atol, rtol):
        if not math.isfinite(value) or value < 0:
            raise ValueError("Tolerances must be finite and non-negative.")
    if reference.shape != actual.shape or not reference.size:
        return {"passed": False, "reason": "shape mismatch or empty image"}
    if not np.isfinite(reference).all() or not np.isfinite(actual).all():
        return {"passed": False, "reason": "NaN or infinity"}
    delta = np.abs(reference.astype(np.float64) - actual.astype(np.float64))
    bad = delta > atol + rtol * np.abs(reference)
    return {"passed": not bool(bad.any()), "max_abs": float(delta.max()),
            "mean_abs": float(delta.mean()), "rmse": float(np.sqrt((delta ** 2).mean())),
            "max_abs_rgb": delta.max(axis=(0, 1)).tolist(), "failed_samples": int(bad.sum()),
            "atol": atol, "rtol": rtol}


def display_pixels(config, pixels, display, view):
    import numpy as np
    import PyOpenColorIO as ocio
    transform = ocio.DisplayViewTransform(src="ACEScg", display=display, view=view)
    processor = config.getProcessor(transform).getDefaultCPUProcessor()
    result = np.ascontiguousarray(pixels.copy(), dtype=np.float32)
    processor.applyRGB(result)
    return result


def write_preview(path, pixels):
    import numpy as np
    import OpenImageIO as oiio
    # Display transform has already been applied. No second gamma operation.
    data = np.ascontiguousarray(np.rint(np.clip(pixels, 0, 1) * 255), dtype=np.uint8)
    output = oiio.ImageOutput.create(str(path))
    if not output or not output.open(str(path), oiio.ImageSpec(data.shape[1], data.shape[0], 3, oiio.UINT8)):
        raise RuntimeError(f"Cannot write preview: {path}")
    try:
        if not output.write_image(data):
            raise RuntimeError(output.geterror())
    finally:
        output.close()
