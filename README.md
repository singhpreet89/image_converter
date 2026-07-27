# Image Converter

A command-line tool that batch-converts images from one format to another (e.g. PNG → WebP, JPEG → PNG) using [Pillow](https://python-pillow.org/). Point it at a directory of source images and it converts every matching file into an output directory, logging progress and any failures along the way.

## Features

- Batch-converts an entire directory (optionally recursive) in one run
- Filter which source extensions get picked up, or convert any format Pillow recognizes
- Converts to any format Pillow can write (webp, jpg, png, bmp, tiff, gif, ico, ...)
- Handles transparency correctly when converting to formats that don't support alpha (e.g. flattens RGBA onto a white background for JPEG/BMP)
- Won't clobber existing output files by default — auto-renames instead (`photo_1.webp`, `photo_2.webp`, ...)
- `--dry-run` mode to preview what would happen without writing anything
- Every run appends a log to `storage/logs/<APP_NAME>.log`

## Requirements

- Python 3.9+
- `pip` and `venv` (standard with Python)
- Dependencies (installed automatically via `make setup`): `Pillow`, `python-dotenv`

## Setup

```bash
make setup
```

This creates a `.venv` virtual environment and installs the dependencies from `requirements.txt`.

You don't need to run this manually every time: `make run`/`make dry-run` depend on it
automatically, creating the venv if it's missing and reinstalling dependencies whenever
`requirements.txt` changes.

Then create your own `.env` file from the example:

```bash
cp .env.example .env
```

Edit `.env` and set at least `INPUT_DIR` and `OUTPUT_DIR`.

## Running

```bash
make run
```

This runs `convert.py` using whatever is configured in `.env`.

Preview a run without writing any files:

```bash
make dry-run
```

Pass extra CLI args (these override `.env` for that run) via `ARGS`:

```bash
make run ARGS="--input /path/to/source --output /path/to/converted --format png"
```

Or run the script directly (with the venv active or via its python):

```bash
.venv/bin/python convert.py --input /path/to/source --output /path/to/converted --format webp --quality 100
```

### Other Makefile targets

| Command           | Description                                              |
|--------------------|------------------------------------------------------------|
| `make setup`       | Create the venv and install dependencies                  |
| `make run`         | Run the converter using `.env` settings (override with `ARGS="..."`) |
| `make dry-run`     | Preview what would be converted without writing any files |
| `make logs`        | Tail the run log (`storage/logs/<APP_NAME>.log`)          |
| `make clean-logs`  | Delete generated log files                                 |
| `make clean`       | Remove the virtual environment                             |

## Configuration (.env variables)

Every setting can be provided via `.env` and overridden per-run with the matching CLI flag.

| Variable         | CLI flag        | Default            | Description |
|-------------------|------------------|---------------------|--------------|
| `APP_NAME`         | `--app-name`     | `image_converter`  | Used to name the log file: `storage/logs/<APP_NAME>.log` |
| `INPUT_DIR`        | `--input`        | *(required)*        | Directory containing the source images to convert. Works with a WSL2-native path or a Windows drive mounted under `/mnt` (e.g. `/mnt/c/Users/YourName/Pictures/source`) |
| `OUTPUT_DIR`       | `--output`       | *(required)*        | Directory where converted images are written. Created automatically if it doesn't exist |
| `SOURCE_FORMATS`   | `--extensions`   | `*`                 | Comma-separated list of source extensions to select (no dots, case-insensitive), or `*`/`all` for any recognized image format. Example: `png,bmp,tiff,gif` |
| `TARGET_FORMAT`    | `--format`       | `webp`              | Format to convert selected files to. Any format Pillow can write: `webp`, `jpg`, `jpeg`, `png`, `bmp`, `tiff`, `gif`, `ico`, ... |
| `QUALITY`          | `--quality`      | `100`                | Output quality, 1-100 (only applies to quality-based formats like JPEG/WebP). Higher = better quality, larger file |
| `OVERWRITE`        | `--overwrite`    | `false`             | If `true`, overwrite existing output files. If `false`, a numbered suffix is added instead (e.g. `photo_1.webp`) so nothing is ever clobbered |
| `RECURSIVE`        | `--recursive`    | `true`             | If `true`, also scan subdirectories of `INPUT_DIR`. Output preserves the same subfolder structure |
| `DRY_RUN`          | `--dry-run`      | `false`             | If `true`, only show what would be converted without writing any files |

`INPUT_DIR` and `OUTPUT_DIR` must be set either in `.env` or via `--input`/`--output` on the command line — the script exits with an error if both are missing.

## Logs

Each run appends to `storage/logs/<APP_NAME>.log`, recording every file converted (or why it failed) plus a summary line at the end (`X/Y images converted, N renamed, M failed`). Tail it live with:

```bash
make logs
```
