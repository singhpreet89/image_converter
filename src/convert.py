import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Optional

from dotenv import load_dotenv
from PIL import Image

from src.manifest import Manifest

load_dotenv()

Image.init()  # populate Image.registered_extensions() / Image.SAVE with all installed plugins

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_ROOT / "storage" / "logs"
STATE_DIR = PROJECT_ROOT / "storage" / "state"
EXT_TO_PIL_FORMAT = Image.registered_extensions()  # e.g. {".png": "PNG", ".jpg": "JPEG", ...}
WRITABLE_FORMATS = {ext.lstrip(".") for ext, fmt in EXT_TO_PIL_FORMAT.items() if fmt in Image.SAVE}
NO_ALPHA_FORMATS = {"JPEG", "BMP"}
QUALITY_FORMATS = {"JPEG", "WEBP"}

# EXIF tag IDs that hold date/time information - the only tags kept when
# --strip-metadata is on. DateTime lives in the top-level IFD; the other
# three live in the "Exif" sub-IFD pointed to by EXIF_SUBIFD_TAG.
EXIF_DATE_TAGS = {
    0x0132,  # DateTime
    0x9003,  # DateTimeOriginal
    0x9004,  # DateTimeDigitized
    0x9290,  # SubsecTime
    0x9291,  # SubsecTimeOriginal
    0x9292,  # SubsecTimeDigitized
}
EXIF_SUBIFD_TAG = 0x8769  # ExifOffset: pointer to the sub-IFD holding DateTimeOriginal etc.


def parse_args():
    parser = argparse.ArgumentParser(description="Batch-convert images between formats.")
    parser.add_argument("--input", default=os.getenv("INPUT_DIR"), help="Directory containing source files")
    parser.add_argument("--output", default=os.getenv("OUTPUT_DIR"), help="Directory to write converted files")
    parser.add_argument(
        "--extensions",
        default=os.getenv("SOURCE_FORMATS", "*"),
        help="Comma-separated list of source file extensions to convert (e.g. png,bmp,tiff), or '*' for any recognized image format",
    )
    parser.add_argument(
        "--format",
        default=os.getenv("TARGET_FORMAT", "webp").lower(),
        help="Target format extension, e.g. webp, jpg, jpeg, png, bmp, tiff, gif",
    )
    parser.add_argument("--quality", type=int, default=int(os.getenv("QUALITY", "100")), help="Output quality (1-100)")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=os.getenv("OVERWRITE", "false").lower() == "true",
        help="Overwrite existing output files",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        default=os.getenv("RECURSIVE", "true").lower() == "true",
        help="Also scan subdirectories of the input directory",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.getenv("DRY_RUN", "false").lower() == "true",
        help="Show what would be converted without writing any files",
    )
    parser.add_argument(
        "--parallel",
        dest="parallel",
        action="store_true",
        default=os.getenv("PARALLEL", "true").lower() == "true",
        help="Convert files in parallel using a process pool (default; overrides PARALLEL=false in .env)",
    )
    parser.add_argument(
        "--sequential",
        dest="parallel",
        action="store_false",
        help="Disable parallelism and convert files one at a time in the main process (overrides PARALLEL=true in .env)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=os.getenv("WORKERS") or None,
        help="Number of parallel worker processes to use when running in parallel (default: all available CPU cores)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=os.getenv("FORCE", "false").lower() == "true",
        help="Ignore any leftover resume manifest from an interrupted run and reconvert every matching file",
    )
    parser.add_argument(
        "--strip-metadata",
        action="store_true",
        default=os.getenv("STRIP_METADATA", "false").lower() == "true",
        help="Strip identifying EXIF metadata (GPS, camera make/model, lens, software, owner, serial numbers) "
        "from output images, keeping only the date/time tags",
    )
    parser.add_argument(
        "--app-name",
        default=os.getenv("APP_NAME", "image_converter"),
        help="Used to name the log file (storage/logs/<app-name>.log) and the resume manifest (storage/state/<app-name>.sqlite3)",
    )
    return parser.parse_args()


def detect_cpu_count() -> int:
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0))
    return os.cpu_count() or 1


def resolve_output_path(dst: Path, overwrite: bool, claimed: "set[Path]") -> Path:
    """Pick the final output path for `dst`, avoiding collisions both with
    pre-existing files on disk and with other destinations already claimed
    earlier in this same run (which matters once conversion is dispatched to
    parallel workers - two different source files can compute the same
    initial destination, e.g. photo.png and photo.jpg both -> photo.webp)."""
    stem, suffix = dst.stem, dst.suffix
    n = 0
    candidate = dst
    while True:
        collides = candidate in claimed or (candidate.exists() and not overwrite)
        if not collides:
            claimed.add(candidate)
            return candidate
        n += 1
        candidate = dst.with_name(f"{stem}_{n}{suffix}")


def build_output_exif(img: Image.Image, strip_metadata: bool) -> Optional[bytes]:
    if not strip_metadata:
        raw = img.info.get("exif")
        return raw if raw else None

    exif = img.getexif()
    if not exif:
        return None

    sub_ifd = exif.get_ifd(EXIF_SUBIFD_TAG)
    for tag in list(sub_ifd.keys()):
        if tag not in EXIF_DATE_TAGS:
            del sub_ifd[tag]

    for tag in list(exif.keys()):
        if tag != EXIF_SUBIFD_TAG and tag not in EXIF_DATE_TAGS:
            del exif[tag]

    return exif.tobytes()


def convert_image(src: Path, dst: Path, fmt: str, quality: int, strip_metadata: bool) -> None:
    pillow_format = EXT_TO_PIL_FORMAT[f".{'jpg' if fmt == 'jpeg' else fmt}"]
    with Image.open(src) as img:
        exif_bytes = build_output_exif(img, strip_metadata)

        if pillow_format in NO_ALPHA_FORMATS:
            if img.mode in ("RGBA", "LA", "P"):
                rgba = img.convert("RGBA")
                background = Image.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.split()[-1])
                img = background
            else:
                img = img.convert("RGB")

        save_kwargs: dict[str, Any] = {"quality": quality} if pillow_format in QUALITY_FORMATS else {}
        if pillow_format == "JPEG":
            save_kwargs["optimize"] = True
        if exif_bytes:
            save_kwargs["exif"] = exif_bytes
        img.save(dst, pillow_format, **save_kwargs)

    src_stat = src.stat()
    os.utime(dst, (src_stat.st_atime, src_stat.st_mtime))


@dataclass
class ConvertJob:
    index: int
    src: Path
    rel: Path
    final_dst: Path
    renamed: bool
    fmt: str
    quality: int
    strip_metadata: bool
    size: int
    mtime: float


@dataclass
class ConvertResult:
    index: int
    rel: Path
    success: bool
    renamed: bool = False
    final_name: str = ""
    error: str = ""
    src_path: str = ""
    size: int = 0
    mtime: float = 0.0
    dst_path: str = ""


def _process_job(job: ConvertJob) -> ConvertResult:
    try:
        job.final_dst.parent.mkdir(parents=True, exist_ok=True)
        convert_image(job.src, job.final_dst, job.fmt, job.quality, job.strip_metadata)
        return ConvertResult(
            index=job.index,
            rel=job.rel,
            success=True,
            renamed=job.renamed,
            final_name=job.final_dst.name,
            src_path=str(job.src),
            size=job.size,
            mtime=job.mtime,
            dst_path=str(job.final_dst),
        )
    except Exception as e:
        return ConvertResult(index=job.index, rel=job.rel, success=False, error=str(e))


def make_logger(log_fh: Optional[IO[str]]):
    def log(msg: str = "") -> None:
        print(msg)
        if log_fh:
            log_fh.write(msg + "\n")

    return log


class Converter:
    def boot(self) -> None:
        args = parse_args()

        if not args.input or not args.output:
            sys.exit("INPUT_DIR/--input and OUTPUT_DIR/--output must be set (via .env or CLI args).")

        input_dir = Path(args.input).expanduser().resolve()
        output_dir = Path(args.output).expanduser().resolve()

        if not input_dir.is_dir():
            sys.exit(f"Input directory does not exist: {input_dir}")

        target_ext = "jpg" if args.format == "jpeg" else args.format
        if target_ext not in WRITABLE_FORMATS:
            sys.exit(f"Unsupported target format '{args.format}'. Examples: webp, jpg, jpeg, png, bmp, tiff, gif")

        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_file = LOGS_DIR / f"{args.app_name}.log"
        log_fh = open(log_file, "a")
        log = make_logger(log_fh)

        manifest_path = STATE_DIR / f"{args.app_name}.sqlite3"
        manifest: Optional[Manifest] = None

        try:
            manifest = Manifest(manifest_path, read_only=args.dry_run)

            extensions_arg = args.extensions.strip().lower()
            if extensions_arg in ("*", "all"):
                source_exts = {ext.lstrip(".") for ext in EXT_TO_PIL_FORMAT}
            else:
                source_exts = {e.strip().lstrip(".") for e in extensions_arg.split(",") if e.strip()}

            candidates = input_dir.rglob("*") if args.recursive else input_dir.iterdir()
            source_files = sorted(
                p for p in candidates if p.is_file() and p.suffix.lower().lstrip(".") in source_exts
            )

            if not source_files:
                log(f"No files with extension(s) {args.extensions} found in {input_dir}")
                return

            if not args.dry_run:
                output_dir.mkdir(parents=True, exist_ok=True)

            ext = target_ext
            output_dir_key = str(output_dir)
            total = len(source_files)
            converted, renamed, skipped = 0, 0, 0
            failures: list[tuple[str, str]] = []
            jobs: list[ConvertJob] = []
            claimed: "set[Path]" = set()

            for i, src in enumerate(source_files, start=1):
                rel = src.relative_to(input_dir)
                dst = output_dir / rel.parent / f"{src.stem}.{ext}"
                src_stat = src.stat()

                if not args.force and manifest.is_converted(
                    str(src), ext, output_dir_key, src_stat.st_size, src_stat.st_mtime
                ):
                    skipped += 1
                    if args.dry_run:
                        log(f"{i}. [dry-run] already converted (skip): {rel}")
                    continue

                if args.dry_run:
                    log(f"{i}. [dry-run] would convert: {rel} -> {(rel.parent / dst.name)}")
                    converted += 1
                    continue

                final_dst = resolve_output_path(dst, args.overwrite, claimed)
                jobs.append(
                    ConvertJob(
                        index=i,
                        src=src,
                        rel=rel,
                        final_dst=final_dst,
                        renamed=final_dst != dst,
                        fmt=args.format,
                        quality=args.quality,
                        strip_metadata=args.strip_metadata,
                        size=src_stat.st_size,
                        mtime=src_stat.st_mtime,
                    )
                )

            if args.dry_run:
                log(f"\n[dry-run] Done. {converted}/{total} would convert, {skipped} already converted (skip).")
                return

            if not jobs:
                log(f"\nDone. 0/{total} converted, {skipped} already converted (skip), 0 failed.")
                return

            def handle_result(r: ConvertResult) -> None:
                nonlocal converted, renamed
                if r.success:
                    log(f"[{r.index}/{total}] Converted: {r.rel} -> {(r.rel.parent / r.final_name)}")
                    converted += 1
                    if r.renamed:
                        renamed += 1
                    # Record immediately, not after the whole batch finishes - this is what
                    # makes a killed/crashed run resumable instead of losing all progress.
                    manifest.record(r.src_path, ext, output_dir_key, r.size, r.mtime, r.dst_path, time.time())
                else:
                    log(f"[{r.index}/{total}] failed: {r.rel} ({r.error})")
                    failures.append((str(r.rel), r.error))

            if args.parallel and len(jobs) > 1:
                workers = min(args.workers or detect_cpu_count(), len(jobs))
                log(f"Converting {len(jobs)} file(s) using {workers} parallel worker process(es)...")
                with ProcessPoolExecutor(max_workers=workers) as executor:
                    futures = [executor.submit(_process_job, job) for job in jobs]
                    for future in as_completed(futures):
                        handle_result(future.result())
            else:
                log(f"Converting {len(jobs)} file(s) sequentially...")
                for job in jobs:
                    handle_result(_process_job(job))

            log(
                f"\nDone. {converted}/{total} images converted ({renamed} renamed to avoid clobbering, "
                f"{skipped} already converted/skipped), {len(failures)} failed."
            )
            if failures:
                log("\nFiles that could not be converted:")
                for name, reason in failures:
                    log(f"  - {name}: {reason}")
        finally:
            if log_fh:
                log_fh.close()
            if manifest:
                manifest.delete_file()
