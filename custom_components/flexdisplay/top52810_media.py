"""Read only bounded regular media files; never follow symlinks or fetch URLs."""

import os
from pathlib import PurePosixPath
import stat

MAX_IMAGE_BYTES = 5 * 1024 * 1024


def read_media_image(path: str) -> bytes:
    parts = PurePosixPath(path).parts
    if (not path.startswith("/media/") or ".." in parts
            or len(parts) < 3 or parts[:2] != ("/", "media")):
        raise ValueError("image_file must be a file under /media")
    # Walk each directory using descriptors so links cannot escape the root,
    # including when a directory entry is swapped during the read.
    directory = os.open("/media", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[2:-1]:
            next_directory = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
            )
            os.close(directory)
            directory = next_directory
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_IMAGE_BYTES:
                raise ValueError("Choose a regular image file of at most 5 MiB")
            raw = source.read(MAX_IMAGE_BYTES + 1)
            if len(raw) > MAX_IMAGE_BYTES:
                raise ValueError("Image exceeds 5 MiB")
            return raw
    finally:
        os.close(directory)
