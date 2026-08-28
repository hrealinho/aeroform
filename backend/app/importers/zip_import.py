from pathlib import PurePosixPath
import zipfile
from app.importers.formats import activity_format

MAX_FILES = 20000
# Header-declared sizes come from the archive producer and cannot be trusted, so these
# are a cheap first filter only; extract_member enforces the real limit while writing.
MAX_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
MAX_SINGLE_FILE_BYTES = 200 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024


def _normalized_path(name: str) -> PurePosixPath:
    # ZIP paths are slash-separated by convention, but reject Windows-style
    # traversal too rather than trusting the producer.
    return PurePosixPath(name.replace("\\", "/"))


def _is_metadata_member(name: str) -> bool:
    path = _normalized_path(name)
    return "__MACOSX" in path.parts or path.name.startswith("._") or path.name in {".DS_Store", "Thumbs.db"}


def safe_members(zip_path: str):
    """Yield supported activity members while rejecting unsafe archives.

    Strava bulk exports commonly contain .fit.gz/.tcx.gz files. Archives created
    on macOS also contain __MACOSX/._* resource forks; these are metadata, not
    activities, and must never be parsed as gzip activity files.
    """
    with zipfile.ZipFile(zip_path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_FILES:
            raise ValueError("Archive contains too many files")
        total = sum(i.file_size for i in infos)
        if total > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("Archive is too large after ZIP decompression")
        for info in infos:
            path = _normalized_path(info.filename)
            if path.is_absolute() or ".." in path.parts or (path.parts and ":" in path.parts[0]):
                raise ValueError("Unsafe archive path")
            if info.file_size > MAX_SINGLE_FILE_BYTES:
                raise ValueError("Archive member exceeds size limit")
            if _is_metadata_member(info.filename):
                continue
            if activity_format(info.filename):
                yield info


def extract_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, target: str, max_bytes: int = MAX_SINGLE_FILE_BYTES) -> None:
    """Copy one archive member to disk, enforcing the size limit as bytes are written.

    safe_members can only check ``info.file_size``, which is a field in the archive that
    a malicious producer controls. Streaming with a hard cap means a member that declares
    1 KB and expands to gigabytes is stopped at the limit instead of filling the disk.
    """
    written = 0
    with zf.open(info) as src, open(target, "wb") as dst:
        while True:
            chunk = src.read(COPY_CHUNK_BYTES)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                raise ValueError(f"Archive member {info.filename!r} exceeds the {max_bytes} byte extraction limit")
            dst.write(chunk)
