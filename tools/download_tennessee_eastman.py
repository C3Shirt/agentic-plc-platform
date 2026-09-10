from __future__ import annotations

import argparse
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

BASE_URL = "https://depts.washington.edu/control/LARRY/TE"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download Tennessee Eastman assets for local trace replay."
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=Path("external/tennessee_eastman"),
        help="local asset directory",
    )
    parser.add_argument("--idv", default="idv1", help="IDV archive to download")
    parser.add_argument("--force", action="store_true", help="overwrite downloads")
    parser.add_argument(
        "--no-extract",
        action="store_true",
        help="download only; do not extract trace/table archives",
    )
    args = parser.parse_args()

    target = args.target
    target.mkdir(parents=True, exist_ok=True)
    downloads = {
        "temexd_mod.zip": "temexd_mod.zip",
        "tecode.zip": "tecode.zip",
        "tables.zip": "tables.zip",
        "IDVs/format.txt": "IDVs_format.txt",
        f"IDVs/{args.idv}.zip": f"IDVs_{args.idv}.zip",
    }
    for remote_name, local_name in downloads.items():
        download_file(f"{BASE_URL}/{remote_name}", target / local_name, args.force)

    if not args.no_extract:
        extract_archive(target / f"IDVs_{args.idv}.zip", target / "extracted")
        extract_archive(target / "tables.zip", target / "extracted" / "tables")
    print(f"downloaded Tennessee Eastman assets to {target.resolve()}")
    return 0


def download_file(url: str, destination: Path, force: bool) -> None:
    if destination.exists() and not force:
        print(f"skip existing {destination}")
        return
    print(f"download {url}")
    with urllib.request.urlopen(url, timeout=30) as response:
        destination.write_bytes(response.read())


def extract_archive(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    unzip = shutil.which("unzip")
    if unzip:
        subprocess.run(
            [unzip, "-o", str(archive), "-d", str(destination)],
            check=True,
        )
        return
    try:
        with zipfile.ZipFile(archive) as zip_file:
            zip_file.extractall(destination)
    except NotImplementedError as exc:
        raise SystemExit(
            f"{archive} uses an old ZIP compression method. Install an unzip "
            "binary or extract it manually."
        ) from exc


if __name__ == "__main__":
    raise SystemExit(main())
