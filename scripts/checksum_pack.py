"""Record the challenge pack's identity without committing the pack.

    python scripts/checksum_pack.py --pack packs/official

Writes the file list and SHA-256 of every file (plus the archive) into
docs/data-manifest.md. This is how the repository proves which data version
produced our numbers while keeping organizer materials out of git.

Run this once when the pack arrives, and again if the organizers reissue it.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Checksum the challenge pack")
    parser.add_argument("--pack", type=Path, default=Path("packs/official"))
    parser.add_argument("--out", type=Path, default=Path("docs/data-manifest.md"))
    args = parser.parse_args()

    # TODO: walk the pack, compute checksums, render the manifest table,
    #       and splice it into the manifest doc under the generated-section marker
    raise NotImplementedError


if __name__ == "__main__":
    main()
