"""QuickCast - Python native edition."""
# Single source of truth for the build's version. Must match the
# `filevers`/`prodvers` tuple + version strings in data/version.txt and
# the GitHub release tag (e.g. v1.0.3). Update-checker reads this and
# compares it to the tag returned by api.github.com/.../releases/latest.
__version__ = "1.1.1"
