from pathlib import Path
import zipfile

SUPPORTED = {".fit", ".gpx", ".tcx"}
MAX_FILES = 20000
MAX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024 * 1024
MAX_SINGLE_FILE_BYTES = 500 * 1024 * 1024


def safe_members(zip_path: str):
    with zipfile.ZipFile(zip_path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_FILES:
            raise ValueError("Archive contains too many files")
        total = sum(i.file_size for i in infos)
        if total > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("Archive is too large after decompression")
        for info in infos:
            path = Path(info.filename)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Unsafe archive path")
            if info.file_size > MAX_SINGLE_FILE_BYTES:
                raise ValueError("Archive member exceeds size limit")
            if path.suffix.lower() in SUPPORTED:
                yield info
