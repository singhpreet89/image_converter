import argparse
import os
import sys
from pathlib import Path
from typing import IO, Optional

from dotenv import load_dotenv
from PIL import Image

load_dotenv()

Image.init()  # populate Image.registered_extensions() / Image.SAVE with all installed plugins

LOGS_DIR = Path(__file__).resolve().parent / "storage" / "logs"
EXT_TO_PIL_FORMAT = Image.registered_extensions()  # e.g. {".png": "PNG", ".jpg": "JPEG", ...}
WRITABLE_FORMATS = {ext.lstrip(".") for ext, fmt in EXT_TO_PIL_FORMAT.items() if fmt in Image.SAVE}
NO_ALPHA_FORMATS = {"JPEG", "BMP"}
QUALITY_FORMATS = {"JPEG", "WEBP"}


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
        "--app-name",
        default=os.getenv("APP_NAME", "image_converter"),
        help="Used to name the log file: storage/logs/<app-name>.log",
    )
    return parser.parse_args()


def resolve_output_path(dst: Path, overwrite: bool) -> Path:
    if overwrite or not dst.exists():
        return dst
    stem, suffix = dst.stem, dst.suffix
    n = 1
    while True:
        candidate = dst.with_name(f"{stem}_{n}{suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def convert_image(src: Path, dst: Path, fmt: str, quality: int) -> None:
    pillow_format = EXT_TO_PIL_FORMAT[f".{'jpg' if fmt == 'jpeg' else fmt}"]
    with Image.open(src) as img:
        if pillow_format in NO_ALPHA_FORMATS:
            if img.mode in ("RGBA", "LA", "P"):
                rgba = img.convert("RGBA")
                background = Image.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.split()[-1])
                img = background
            else:
                img = img.convert("RGB")

        save_kwargs = {"quality": quality} if pillow_format in QUALITY_FORMATS else {}
        if pillow_format == "JPEG":
            save_kwargs["optimize"] = True
        img.save(dst, pillow_format, **save_kwargs)


def make_logger(log_fh: Optional[IO[str]]):
    def log(msg: str = "") -> None:
        print(msg)
        if log_fh:
            log_fh.write(msg + "\n")

    return log


def main():
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

    try:
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
        total = len(source_files)
        converted, renamed = 0, 0
        failures: list[tuple[str, str]] = []

        for i, src in enumerate(source_files, start=1):
            rel = src.relative_to(input_dir)
            dst = output_dir / rel.parent / f"{src.stem}.{ext}"

            if args.dry_run:
                log(f"{i}. [dry-run] would convert: {rel} -> {(rel.parent / dst.name)}")
                converted += 1
                continue

            dst.parent.mkdir(parents=True, exist_ok=True)
            final_dst = resolve_output_path(dst, args.overwrite)
            if final_dst != dst:
                renamed += 1
            try:
                convert_image(src, final_dst, args.format, args.quality)
                log(f"{i}. Converted: {rel} -> {(rel.parent / final_dst.name)}")
                converted += 1
            except Exception as e:
                log(f"{i}. failed: {rel} ({e})")
                failures.append((str(rel), str(e)))

        prefix = "[dry-run] " if args.dry_run else ""
        log(f"\n{prefix}Done. {converted}/{total} images converted ({renamed} renamed to avoid clobbering), {len(failures)} failed.")
        if failures:
            log("\nFiles that could not be converted:")
            for name, reason in failures:
                log(f"  - {name}: {reason}")
    finally:
        if log_fh:
            log_fh.close()


if __name__ == "__main__":
    main()
