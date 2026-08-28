from pathlib import Path

def activity_format(name: str) -> tuple[str, bool] | None:
    """Return (inner extension, gzip-compressed) for a supported activity file."""
    lower = name.lower()
    for ext in (".fit.gz", ".gpx.gz", ".tcx.gz"):
        if lower.endswith(ext):
            return ext[:-3], True
    for ext in (".fit", ".gpx", ".tcx"):
        if lower.endswith(ext):
            return ext, False
    return None


def safe_basename(name: str) -> str:
    return Path(name).name.replace("/", "_").replace("\\", "_")
