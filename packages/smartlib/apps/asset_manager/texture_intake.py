"""DCC-independent texture intake. No image conversion or filename rewriting."""
from dataclasses import dataclass
from pathlib import Path
import re


IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.exr', '.hdr',
                    '.tga', '.bmp', '.tx', '.tex', '.dds'}
TEXTURE_USAGES = ('unspecified', 'base_color', 'roughness', 'metallic', 'normal',
                  'opacity', 'displacement', 'emission', 'occlusion', 'other')
COLOR_SPACES = ('unspecified', 'sRGB', 'raw', 'linear', 'ACEScg')


@dataclass(frozen=True)
class TextureInput:
    path: Path
    usage: str = 'unspecified'
    color_space: str = 'unspecified'

    @property
    def udim(self):
        match = re.search(r'(?:^|[._-])(1\d{3})(?=\.[^.]+$)', self.path.name)
        return int(match.group(1)) if match else None


def collect_textures(paths):
    """Return unique images and ignored folder contents; reject basename collisions.

    Publish packages are flat. Folder recursion is input discovery only; filenames
    must be unique across the complete selection, including case on Windows.
    """
    entries, ignored, names, seen = [], [], {}, set()
    for value in paths:
        source = Path(value).resolve(strict=True)
        candidates = sorted(source.rglob('*')) if source.is_dir() else [source]
        for candidate in candidates:
            if not candidate.is_file():
                continue
            path = candidate.resolve(strict=True)
            if path in seen:
                continue
            seen.add(path)
            if path.suffix.lower() not in IMAGE_EXTENSIONS:
                if not source.is_dir():
                    raise ValueError(f'Unsupported texture file: {path.name}')
                ignored.append(path)
                continue
            if path.stat().st_size == 0:
                raise ValueError(f'Empty texture file: {path}')
            key = path.name.casefold()
            if key in names:
                raise ValueError(f'Duplicate texture filename: {path.name}\n{names[key]}\n{path}')
            names[key] = path
            entries.append(TextureInput(path))
    return entries, ignored


def validate_inputs(entries):
    entries = list(entries)
    if not entries:
        raise ValueError('No textures selected')
    checked, _ = collect_textures(entry.path for entry in entries)
    if len(checked) != len(entries):
        raise ValueError('Duplicate texture input')
    for entry in entries:
        if entry.usage not in TEXTURE_USAGES or entry.color_space not in COLOR_SPACES:
            raise ValueError(f'Invalid texture metadata: {entry.path.name}')
    return entries
