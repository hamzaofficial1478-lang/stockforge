"""Getting finished work to the agencies.

One hard rule runs through this whole package: nothing uploads unless its spec
says `publishable`. That flag is set by the provenance check during analysis and
it is not advisory. A design assembled from a template library's stock art still
gets a full editable master — you own the right to use it, and getting that file
back is half the point of this project — it just never touches an FTP queue.

That is not caution for its own sake. Agencies require the contributor to hold
redistribution rights to every element in a submitted file, they run similarity
matching on what comes in, and the penalty for getting it wrong lands on the
account rather than the file.
"""

from __future__ import annotations

from .metadata import Metadata, adobe_csv, shutterstock_csv, write_metadata
from .ftp import FTPTarget, upload_batch

__all__ = [
    "Metadata", "adobe_csv", "shutterstock_csv", "write_metadata",
    "FTPTarget", "upload_batch",
]
