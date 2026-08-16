#!/usr/bin/env python3
"""
Complete Image Batch Processing Tool

Features:
- Strip metadata (EXIF, ICC profiles, comments, etc.) from images by rebuilding pixel data
- Bulk rename with case-insensitive pattern matching + zero-padded sequential numbers
- Full recursive subdirectory support (-r / --recursive)
- Progress bars powered by tqdm
- Robust error handling (invalid images, permissions, collisions)
- Dry-run mode for safe preview
- Preserves directory structure when using --output with --recursive

Supported formats: .jpg, .jpeg, .jfif, .png, .tiff, .webp, .bmp, .gif

Usage examples are in the accompanying README.md
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple, Optional, Callable, Any

try:
    from PIL import Image, ImageSequence, UnidentifiedImageError
except ImportError:
    print("ERROR: Pillow is required but not installed.")
    print("Install dependencies with: pip install -r requirements.txt")
    sys.exit(1)

try:
    from tqdm import tqdm
except ImportError:
    print("ERROR: tqdm is required but not installed.")
    print("Install dependencies with: pip install -r requirements.txt")
    sys.exit(1)


# Supported formats - images + videos
# .jfif = JPEG File Interchange Format (same codec family as .jpg/.jpeg)
JPEG_EXTS = {".jpg", ".jpeg", ".jfif"}
SUPPORTED_IMAGE_EXTS = JPEG_EXTS | {".png", ".tiff", ".webp", ".bmp", ".gif"}
SUPPORTED_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm"}
SUPPORTED_EXTS = SUPPORTED_IMAGE_EXTS | SUPPORTED_VIDEO_EXTS


LogFunc = Optional[Callable[[str], None]]


def ffmpeg_available() -> bool:
    """Check if ffmpeg and ffprobe are available (required for video ops)."""
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _probe_video_ok(path: Path) -> bool:
    """Verify video file with ffprobe."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode != 0:
            return False
        data = json.loads(r.stdout)
        return bool(data.get("streams"))
    except Exception:
        return False


def _emit(msg: str, log_func: LogFunc = None) -> None:
    """Send a log message either to the provided callback (GUI) or to tqdm/console (CLI)."""
    if log_func is not None:
        try:
            log_func(msg)
        except Exception:
            # Never let logging break the operation
            pass
    else:
        # CLI path: prefer tqdm.write so it plays nice with progress bars
        try:
            from tqdm import tqdm
            tqdm.write(msg)
        except Exception:
            print(msg)


def _effective_output_dir(output_dir: str | None, input_dir: Path) -> Path:
    """Return the resolved directory where files will actually be written.
    If no output_dir, files are written next to originals (in-place semantics).
    """
    if output_dir:
        try:
            return Path(output_dir).resolve()
        except Exception:
            return Path(output_dir)
    return input_dir


def get_media_files(input_dir: Path, recursive: bool, exts: set = None) -> List[Path]:
    """Return sorted list of supported media files (images + videos).

    Sorting is case-insensitive on the full path for deterministic ordering.
    """
    if exts is None:
        exts = SUPPORTED_EXTS
    glob_pattern = "**/*.*" if recursive else "*.*"
    files: List[Path] = []
    for f in input_dir.glob(glob_pattern):
        if f.is_file() and f.suffix.lower() in exts:
            files.append(f)
    return sorted(files, key=lambda p: str(p).lower())


def get_image_files(input_dir: Path, recursive: bool) -> List[Path]:
    """Return sorted list of supported image files (for backward compat)."""
    return get_media_files(input_dir, recursive, SUPPORTED_IMAGE_EXTS)


def get_video_files(input_dir: Path, recursive: bool) -> List[Path]:
    """Return sorted list of supported video files."""
    return get_media_files(input_dir, recursive, SUPPORTED_VIDEO_EXTS)


def _create_clean_image(img: Image.Image) -> Image.Image:
    """Return a pixel-identical copy of the image with *all* metadata removed.

    Uses tobytes/frombytes to avoid the deprecated getdata() API (Pillow 14+).
    Also clears the .info dict for extra safety.
    """
    # First, a clean copy with info nuked (removes tons of sidecar metadata)
    clean = img.copy()
    clean.info = {}

    # Aggressive full pixel rebuild (guarantees EXIF/ICC/XMP etc are gone)
    try:
        raw = img.tobytes()
        rebuilt = Image.frombytes(img.mode, img.size, raw)
        if img.mode == "P" and img.palette is not None:
            rebuilt.palette = img.palette
        # Also nuke info on the rebuilt version
        rebuilt.info = {}
        return rebuilt
    except Exception:
        # Fallback: at least we have the info-cleared copy
        return clean


def strip_metadata(
    input_path: str,
    output_dir: str | None = None,
    recursive: bool = False,
    dry_run: bool = False,
    log_func: LogFunc = None,
) -> Tuple[int, int]:
    """Strip metadata from images.

    Returns (processed_count, error_count).
    When output_dir is given + recursive=True, the original relative folder
    structure is recreated under the output directory.

    If log_func is provided (used by GUI), messages are sent there instead of console.
    """
    input_dir = Path(input_path).resolve()
    if not input_dir.exists() or not input_dir.is_dir():
        _emit(f"ERROR: Directory does not exist or is not a directory: {input_dir}", log_func)
        return 0, 1

    output_path = Path(output_dir).resolve() if output_dir else None
    if output_path:
        output_path.mkdir(parents=True, exist_ok=True)

    image_files = get_image_files(input_dir, recursive)
    if not image_files:
        _emit("No supported image files found.", log_func)
        return 0, 0

    processed = 0
    errors = 0

    desc = "DRY-RUN: Stripping metadata" if dry_run else "Stripping metadata"

    # In GUI mode we avoid tqdm (it would fight with the text log).
    # We still show nice progress via log messages.
    use_tqdm = log_func is None
    iterator = tqdm(image_files, desc=desc, unit="file") if use_tqdm else image_files
    total = len(image_files)

    for idx, img_file in enumerate(iterator, 1):
        if not use_tqdm:
            _emit(f"[{idx}/{total}] Processing: {img_file.name}", log_func)
        try:
            with Image.open(img_file) as img:
                clean_img = _create_clean_image(img)

                # Compute destination
                if output_path:
                    rel = img_file.relative_to(input_dir) if recursive else Path(img_file.name)
                    save_path = output_path / rel
                    save_path.parent.mkdir(parents=True, exist_ok=True)
                else:
                    save_path = img_file

                suffix = img_file.suffix.lower()
                fmt = img.format

                save_kwargs: dict = {}
                if suffix in JPEG_EXTS:
                    if clean_img.mode in ("RGBA", "LA", "P"):
                        clean_img = clean_img.convert("RGB")
                    save_kwargs = {"quality": 95, "optimize": True, "exif": b""}
                    fmt = "JPEG"
                elif suffix == ".png":
                    save_kwargs = {"optimize": True}
                    fmt = fmt or "PNG"
                elif suffix == ".webp":
                    save_kwargs = {"quality": 90}
                    fmt = fmt or "WEBP"
                elif suffix == ".tiff":
                    fmt = fmt or "TIFF"
                elif suffix == ".bmp":
                    fmt = fmt or "BMP"

                if dry_run:
                    _emit(f"[DRY] {img_file}  -->  {save_path}", log_func)
                else:
                    clean_img.save(save_path, format=fmt, **save_kwargs)
                    _emit(f"Stripped: {img_file.name}", log_func)

                processed += 1

        except UnidentifiedImageError:
            _emit(f"Skipped (not a valid image): {img_file.name}", log_func)
            errors += 1
        except (OSError, IOError) as e:
            _emit(f"IO error on {img_file.name}: {e}", log_func)
            errors += 1
        except Exception as e:
            _emit(f"Unexpected error on {img_file.name}: {e}", log_func)
            errors += 1

    return processed, errors


def batch_rename(
    input_path: str,
    pattern: str,
    new_prefix: str,
    start_num: int = 1,
    recursive: bool = False,
    dry_run: bool = False,
    log_func: LogFunc = None,
) -> Tuple[int, int]:
    """Rename files whose names contain the pattern (case-insensitive).

    Sequential numbers are zero-padded to 3 digits.
    Works correctly inside subdirectories when recursive=True (names stay next to originals).
    Returns (renamed_count, skipped_or_error_count).

    If log_func is provided (used by GUI), messages are sent there instead of console.
    """
    input_dir = Path(input_path).resolve()
    if not input_dir.exists() or not input_dir.is_dir():
        _emit(f"ERROR: Directory does not exist or is not a directory: {input_dir}", log_func)
        return 0, 1

    all_images = get_image_files(input_dir, recursive)
    matching = [f for f in all_images if pattern.lower() in f.name.lower()]

    if not matching:
        _emit(f"No image files containing pattern '{pattern}' were found.", log_func)
        return 0, 0

    processed = 0
    skipped = 0

    desc = "DRY-RUN: Renaming files" if dry_run else "Renaming files"

    use_tqdm = log_func is None
    iterator = tqdm(matching, desc=desc, unit="file") if use_tqdm else matching
    total = len(matching)

    for i, old_file in enumerate(iterator, start=start_num):
        current = i - start_num + 1
        if not use_tqdm:
            _emit(f"[{current}/{total}] Renaming: {old_file.name}", log_func)
        ext = old_file.suffix
        new_name = f"{new_prefix}{i:03d}{ext}"
        new_file = old_file.parent / new_name  # keep inside the same folder

        try:
            if dry_run:
                rel_old = old_file.relative_to(input_dir)
                _emit(f"[DRY] {rel_old}  -->  {new_name}", log_func)
                processed += 1
                continue

            if new_file.exists():
                _emit(f"Skipped (already exists): {new_name} (would replace {old_file.name})", log_func)
                skipped += 1
                continue

            old_file.rename(new_file)
            _emit(f"Renamed: {old_file.name} -> {new_name}", log_func)
            processed += 1

        except Exception as e:
            _emit(f"Error renaming {old_file.name}: {e}", log_func)
            skipped += 1

    return processed, skipped


# ============================================================
# NEW FEATURES: Compression, Resize, Trim, Mute (ported + extended from media_compressor good logic)
# Quality/speed balance: WebP method=6, x265 preset=medium/slow, CRF for quality control
# ============================================================


def compress_image(
    input_path: str,
    quality: int = 80,
    target_size: int | None = None,
    output_dir: str | None = None,
    recursive: bool = False,
    dry_run: bool = False,
    overwrite: bool = False,
    delete_exts: list = None,
    log_func: LogFunc = None,
) -> Tuple[int, int]:
    """Compress images to WebP using Pillow (quality + method=6 for good compression/speed balance).
    If target_size (bytes) is given, binary search quality to meet or beat the target size."""
    input_dir = Path(input_path).resolve()
    if not input_dir.exists() or not input_dir.is_dir():
        _emit(f"ERROR: Directory does not exist: {input_dir}", log_func)
        return 0, 1

    output_path = Path(output_dir).resolve() if output_dir else None
    if output_path:
        output_path.mkdir(parents=True, exist_ok=True)

    image_files = get_image_files(input_dir, recursive)
    if not image_files:
        _emit("No image files found for compression.", log_func)
        return 0, 0

    processed = 0
    errors = 0
    desc = "DRY-RUN: Compressing images" if dry_run else "Compressing images to WebP"
    use_tqdm = log_func is None
    iterator = tqdm(image_files, desc=desc, unit="file") if use_tqdm else image_files
    total = len(image_files)

    for idx, img_file in enumerate(iterator, 1):
        if not use_tqdm:
            _emit(f"[{idx}/{total}] Compressing: {img_file.name}", log_func)
        save_path = None
        try:
            orig_size = img_file.stat().st_size
            if output_path:
                rel = img_file.relative_to(input_dir) if recursive else Path(img_file.name)
                stem = rel.stem
                save_dir = output_path / rel.parent
                save_dir.mkdir(parents=True, exist_ok=True)
            else:
                stem = img_file.stem
                save_dir = img_file.parent

            if overwrite:
                save_path = save_dir / (stem + ".webp")
            else:
                save_path = save_dir / (stem + "_compressed.webp")

            final_quality = quality
            if target_size and not dry_run:
                # Binary search for quality to meet target size
                low, high = 1, 100
                best_size = None
                best_q = quality
                while low <= high:
                    mid = (low + high) // 2
                    with Image.open(img_file) as im:
                        if im.mode in ("P", "LA"):
                            im = im.convert("RGBA")
                        elif im.mode not in ("RGB", "RGBA"):
                            im = im.convert("RGB")
                        im.save(save_path, "WEBP", quality=mid, method=6, lossless=False)
                    new_size = save_path.stat().st_size
                    if new_size <= target_size:
                        best_q = mid
                        best_size = new_size
                        low = mid + 1  # try higher quality (larger size)
                    else:
                        high = mid - 1
                final_quality = best_q
                if best_size is None:
                    # fallback to given
                    final_quality = quality

            if dry_run:
                ts = f" target={target_size}" if target_size else ""
                _emit(f"[DRY] {img_file} -> {save_path} (quality={final_quality}{ts})", log_func)
                # simulate delete-ext even in dry-run
                dels = delete_exts or []
                orig_ext = img_file.suffix.lower().lstrip('.')
                if orig_ext in dels:
                    _emit(f"[DRY] would delete original {img_file.name} after success", log_func)
                processed += 1
                continue

            with Image.open(img_file) as im:
                is_animated = getattr(im, "is_animated", False)
                if is_animated:
                    frames = [f.convert("RGBA") for f in ImageSequence.Iterator(im)]
                    durations = [f.info.get("duration", 100) for f in frames]
                    frames[0].save(
                        save_path, "WEBP", save_all=True, append_images=frames[1:],
                        quality=final_quality, method=6, lossless=False,
                        loop=im.info.get("loop", 0), duration=durations
                    )
                else:
                    if im.mode in ("P", "LA"):
                        im = im.convert("RGBA")
                    elif im.mode not in ("RGB", "RGBA"):
                        im = im.convert("RGB")
                    im.save(save_path, "WEBP", quality=final_quality, method=6, lossless=False)

            processed += 1
            new_size = save_path.stat().st_size
            ratio = (1 - new_size / orig_size) * 100 if orig_size > 0 else 0
            _emit(f"Compressed: {img_file.name} -> {save_path.name} ({orig_size/1024:.1f}KB -> {new_size/1024:.1f}KB, {ratio:.0f}% saved)", log_func)

            # delete original if requested (real only; dry handled earlier)
            dels = delete_exts or []
            if not dry_run and dels:
                orig_ext = img_file.suffix.lower().lstrip('.')
                if orig_ext in dels:
                    try:
                        img_file.unlink()
                        _emit(f"Deleted original: {img_file.name}", log_func)
                    except Exception as e:
                        _emit(f"Failed to delete original {img_file.name}: {e}", log_func)
        except Exception as e:
            _emit(f"Error compressing {img_file.name}: {e}", log_func)
            errors += 1
            if save_path and save_path.exists():
                try:
                    save_path.unlink()
                except Exception:
                    pass

    return processed, errors


def compress_video(
    input_path: str,
    crf: int = 28,
    preset: str = "medium",
    output_dir: str | None = None,
    recursive: bool = False,
    dry_run: bool = False,
    mute: bool = False,
    overwrite: bool = False,
    delete_exts: list = None,
    log_func: LogFunc = None,
) -> Tuple[int, int]:
    """Compress videos to H.265 MP4 using ffmpeg (CRF + preset for quality/speed balance)."""
    if not ffmpeg_available():
        _emit("ERROR: ffmpeg not found. Install with: sudo apt install ffmpeg", log_func)
        return 0, 1

    input_dir = Path(input_path).resolve()
    if not input_dir.exists() or not input_dir.is_dir():
        _emit(f"ERROR: Directory does not exist: {input_dir}", log_func)
        return 0, 1

    output_path = Path(output_dir).resolve() if output_dir else None
    if output_path:
        output_path.mkdir(parents=True, exist_ok=True)

    # === VIDEO SAFETY: same dir + overwrite is extremely dangerous (file loss risk) ===
    eff_out = _effective_output_dir(output_dir, input_dir)
    if eff_out == input_dir and overwrite:
        _emit("*** WARNING: Input Folder == Output location AND --overwrite=True ***", log_func)
        _emit("For VIDEO processing this means originals may be overwritten in place (or replaced after delete-ext).", log_func)
        _emit("FILE LOSS PREVENTION IS PRIORITY. Skipping the entire operation.", log_func)
        _emit("Suggestion: Use --output to a DIFFERENT folder, or run without --overwrite (will create *_compressed.mp4 etc).", log_func)
        return 0, 0
    if eff_out == input_dir:
        _emit("Note: writing into same Input folder (using _suffix by default). Safe unless --overwrite or --delete-ext.", log_func)

    video_files = get_video_files(input_dir, recursive)
    if not video_files:
        _emit("No video files found for compression.", log_func)
        return 0, 0

    processed = 0
    errors = 0
    desc = "DRY-RUN: Compressing videos" if dry_run else "Compressing videos (H.265)"
    use_tqdm = log_func is None
    iterator = tqdm(video_files, desc=desc, unit="file") if use_tqdm else video_files
    total = len(video_files)

    for idx, vid_file in enumerate(iterator, 1):
        if not use_tqdm:
            _emit(f"[{idx}/{total}] Compressing video: {vid_file.name}", log_func)
        try:
            if output_path:
                rel = vid_file.relative_to(input_dir) if recursive else Path(vid_file.name)
                stem = rel.stem
                save_dir = output_path / rel.parent
                save_dir.mkdir(parents=True, exist_ok=True)
            else:
                stem = vid_file.stem
                save_dir = vid_file.parent

            if overwrite:
                save_path = save_dir / (stem + ".mp4")
            else:
                save_path = save_dir / (stem + "_compressed.mp4")

            if dry_run:
                mute_str = " (muted)" if mute else ""
                _emit(f"[DRY] {vid_file} -> {save_path} (crf={crf}, preset={preset}{mute_str})", log_func)
                dels = delete_exts or []
                orig_ext = vid_file.suffix.lower().lstrip('.')
                if orig_ext in dels:
                    _emit(f"[DRY] would delete original {vid_file.name} after success", log_func)
                processed += 1
                continue

            cmd = [
                "ffmpeg", "-y", "-i", str(vid_file),
                "-c:v", "libx265", "-crf", str(crf), "-preset", preset,
                "-c:a", "aac", "-b:a", "128k",
                "-tag:v", "hvc1", "-movflags", "+faststart",
            ]
            if mute:
                cmd += ["-an"]
            cmd += [str(save_path)]

            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
            if r.returncode != 0 or not save_path.exists():
                _emit(f"ffmpeg error on {vid_file.name}: {r.stderr[-500:]}", log_func)
                save_path.unlink(missing_ok=True)
                errors += 1
                continue

            if not _probe_video_ok(save_path):
                _emit(f"Verification failed for {save_path.name}", log_func)
                save_path.unlink(missing_ok=True)
                errors += 1
                continue

            processed += 1
            _emit(f"Compressed video: {vid_file.name} -> {save_path.name}", log_func)

            # delete if requested (dry sim handled before continue)
            dels = delete_exts or []
            if not dry_run and dels:
                orig_ext = vid_file.suffix.lower().lstrip('.')
                if orig_ext in dels:
                    try:
                        vid_file.unlink()
                        _emit(f"Deleted original: {vid_file.name}", log_func)
                    except Exception as e:
                        _emit(f"Failed to delete original {vid_file.name}: {e}", log_func)
        except Exception as e:
            _emit(f"Error on video {vid_file.name}: {e}", log_func)
            errors += 1

    return processed, errors


def resize_media(
    input_path: str,
    width: int | None = None,
    height: int | None = None,
    scale: float | None = None,
    output_dir: str | None = None,
    recursive: bool = False,
    dry_run: bool = False,
    overwrite: bool = False,
    delete_exts: list = None,
    log_func: LogFunc = None,
) -> Tuple[int, int]:
    """Resize images (PIL) and videos (ffmpeg scale). Keeps aspect if only one dim given."""
    input_dir = Path(input_path).resolve()
    if not input_dir.exists() or not input_dir.is_dir():
        _emit(f"ERROR: Directory does not exist: {input_dir}", log_func)
        return 0, 1

    output_path = Path(output_dir).resolve() if output_dir else None
    if output_path:
        output_path.mkdir(parents=True, exist_ok=True)

    # === VIDEO SAFETY (resize can affect videos) ===
    eff_out = _effective_output_dir(output_dir, input_dir)
    if eff_out == input_dir and overwrite:
        _emit("*** WARNING: Input == Output dir + --overwrite for resize ***", log_func)
        _emit("VIDEO originals could be lost (in-place replace). Skipping whole op for safety.", log_func)
        _emit("Use different --output folder or omit --overwrite.", log_func)
        return 0, 1
    if eff_out == input_dir:
        _emit("Note: resize will write into input folder (suffix or overwrite).", log_func)

    media_files = get_media_files(input_dir, recursive)
    if not media_files:
        _emit("No media files found.", log_func)
        return 0, 0

    processed = 0
    errors = 0
    desc = "DRY-RUN: Resizing" if dry_run else "Resizing media"
    use_tqdm = log_func is None
    iterator = tqdm(media_files, desc=desc, unit="file") if use_tqdm else media_files
    total = len(media_files)

    for idx, mfile in enumerate(iterator, 1):
        if not use_tqdm:
            _emit(f"[{idx}/{total}] Resizing: {mfile.name}", log_func)
        try:
            is_vid = mfile.suffix.lower() in SUPPORTED_VIDEO_EXTS
            if output_path:
                rel = mfile.relative_to(input_dir) if recursive else Path(mfile.name)
                stem = rel.stem
                save_dir = output_path / rel.parent
                save_dir.mkdir(parents=True, exist_ok=True)
            else:
                stem = mfile.stem
                save_dir = mfile.parent

            if overwrite:
                save_path = save_dir / (stem + mfile.suffix)
            else:
                save_path = save_dir / (stem + "_resized" + mfile.suffix)

            if dry_run:
                dims = f"scale={scale}" if scale else f"{width}x{height or 'auto'}"
                _emit(f"[DRY] {mfile} -> {save_path} ({dims})", log_func)
                dels = delete_exts or []
                orig_ext = mfile.suffix.lower().lstrip('.')
                if orig_ext in dels:
                    _emit(f"[DRY] would delete original {mfile.name}", log_func)
                processed += 1
                continue

            if is_vid:
                if not ffmpeg_available():
                    _emit("Skipping video resize - no ffmpeg", log_func)
                    errors += 1
                    continue
                # Build scale filter
                if scale:
                    vf = f"scale=iw*{scale}:ih*{scale}"
                elif width and height:
                    vf = f"scale={width}:{height}"
                elif width:
                    vf = f"scale={width}:-1"
                elif height:
                    vf = f"scale=-1:{height}"
                else:
                    continue
                cmd = ["ffmpeg", "-y", "-i", str(mfile), "-vf", vf, "-c:v", "libx265", "-crf", "23", "-preset", "medium", "-c:a", "copy", str(save_path)]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
                if r.returncode != 0:
                    _emit(f"Resize video error: {r.stderr[-300:]}", log_func)
                    errors += 1
                    continue
            else:
                # Image resize with PIL
                with Image.open(mfile) as im:
                    if scale:
                        new_size = (int(im.width * scale), int(im.height * scale))
                    elif width and height:
                        new_size = (width, height)
                    elif width:
                        ratio = width / im.width
                        new_size = (width, int(im.height * ratio))
                    elif height:
                        ratio = height / im.height
                        new_size = (int(im.width * ratio), height)
                    else:
                        continue
                    im_resized = im.resize(new_size, Image.LANCZOS)
                    ext = mfile.suffix.lower()
                    fmt = "JPEG" if ext in JPEG_EXTS else im.format or "PNG"
                    if fmt == "JPEG" and im_resized.mode in ("RGBA", "P"):
                        im_resized = im_resized.convert("RGB")
                    im_resized.save(save_path, format=fmt)

            processed += 1
            _emit(f"Resized: {mfile.name} -> {save_path.name}", log_func)

            # delete (dry sim earlier)
            dels = delete_exts or []
            if not dry_run and dels:
                orig_ext = mfile.suffix.lower().lstrip('.')
                if orig_ext in dels:
                    try:
                        mfile.unlink()
                        _emit(f"Deleted original: {mfile.name}", log_func)
                    except Exception as e:
                        _emit(f"Failed delete {mfile.name}: {e}", log_func)
        except Exception as e:
            _emit(f"Resize error {mfile.name}: {e}", log_func)
            errors += 1

    return processed, errors


def trim_video(
    input_path: str,
    start: str | None = None,
    end: str | None = None,
    output_dir: str | None = None,
    recursive: bool = False,
    dry_run: bool = False,
    overwrite: bool = False,
    delete_exts: list = None,
    log_func: LogFunc = None,
) -> Tuple[int, int]:
    """Trim videos using ffmpeg -ss / -to."""
    if not ffmpeg_available():
        _emit("ERROR: ffmpeg required for trim", log_func)
        return 0, 1
    if not start and not end:
        _emit("ERROR: Provide --start and/or --end for trim", log_func)
        return 0, 1

    input_dir = Path(input_path).resolve()
    video_files = get_video_files(input_dir, recursive)
    if not video_files:
        return 0, 0

    output_path = Path(output_dir).resolve() if output_dir else None
    if output_path:
        output_path.mkdir(parents=True, exist_ok=True)

    # === VIDEO SAFETY ===
    eff_out = _effective_output_dir(output_dir, input_dir)
    if eff_out == input_dir and overwrite:
        _emit("*** WARNING: Same dir + overwrite for VIDEO TRIM! Data loss risk. ***", log_func)
        _emit("Skipping operation to protect originals. Use --output DIFFERENT dir or no --overwrite.", log_func)
        return 0, 0
    if eff_out == input_dir:
        _emit("Note: trim output will land in input dir (with suffix unless overwrite).", log_func)

    processed = 0
    errors = 0
    desc = "DRY-RUN: Trimming videos" if dry_run else "Trimming videos"
    use_tqdm = log_func is None
    iterator = tqdm(video_files, desc=desc, unit="file") if use_tqdm else video_files

    for idx, vid_file in enumerate(iterator, 1):
        if not use_tqdm:
            _emit(f"[{idx}/{len(video_files)}] Trimming: {vid_file.name}", log_func)
        try:
            if output_path:
                rel = vid_file.relative_to(input_dir) if recursive else Path(vid_file.name)
                stem = rel.stem
                save_dir = output_path / rel.parent
                save_dir.mkdir(parents=True, exist_ok=True)
            else:
                stem = vid_file.stem
                save_dir = vid_file.parent

            if overwrite:
                save_path = save_dir / (stem + ".mp4")
            else:
                save_path = save_dir / (stem + "_trimmed.mp4")

            if dry_run:
                _emit(f"[DRY] trim {vid_file} start={start} end={end} -> {save_path}", log_func)
                dels = delete_exts or []
                orig_ext = vid_file.suffix.lower().lstrip('.')
                if orig_ext in dels:
                    _emit(f"[DRY] would delete original {vid_file.name}", log_func)
                processed += 1
                continue

            cmd = ["ffmpeg", "-y", "-i", str(vid_file)]
            if start:
                cmd += ["-ss", start]
            if end:
                cmd += ["-to", end]
            cmd += ["-c:v", "libx265", "-crf", "23", "-preset", "medium", "-c:a", "copy", str(save_path)]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
            if r.returncode == 0 and _probe_video_ok(save_path):
                processed += 1
                _emit(f"Trimmed: {vid_file.name} -> {save_path.name}", log_func)

                dels = delete_exts or []
                if not dry_run and dels:
                    orig_ext = vid_file.suffix.lower().lstrip('.')
                    if orig_ext in dels:
                        try:
                            vid_file.unlink()
                            _emit(f"Deleted original: {vid_file.name}", log_func)
                        except Exception as e:
                            _emit(f"Failed delete {vid_file.name}: {e}", log_func)
            else:
                _emit(f"Trim failed: {r.stderr[-300:]}", log_func)
                save_path.unlink(missing_ok=True)
                errors += 1
        except Exception as e:
            _emit(f"Trim error: {e}", log_func)
            errors += 1

    return processed, errors


def mute_video(
    input_path: str,
    output_dir: str | None = None,
    recursive: bool = False,
    dry_run: bool = False,
    overwrite: bool = False,
    delete_exts: list = None,
    log_func: LogFunc = None,
) -> Tuple[int, int]:
    """Remove audio from videos."""
    if not ffmpeg_available():
        _emit("ERROR: ffmpeg required for mute", log_func)
        return 0, 1

    input_dir = Path(input_path).resolve()
    video_files = get_video_files(input_dir, recursive)
    if not video_files:
        return 0, 0

    output_path = Path(output_dir).resolve() if output_dir else None
    if output_path:
        output_path.mkdir(parents=True, exist_ok=True)

    # === VIDEO SAFETY ===
    eff_out = _effective_output_dir(output_dir, input_dir)
    if eff_out == input_dir and overwrite:
        _emit("*** WARNING: Same dir + --overwrite for VIDEO MUTE! Risk of replacing originals. ***", log_func)
        _emit("Skipping to prevent loss. Suggest: separate output folder.", log_func)
        return 0, 0
    if eff_out == input_dir:
        _emit("Note: muted files will be written to input dir (safe _suffix default).", log_func)

    processed = 0
    errors = 0
    desc = "DRY-RUN: Muting audio" if dry_run else "Muting video audio"
    use_tqdm = log_func is None
    iterator = tqdm(video_files, desc=desc, unit="file") if use_tqdm else video_files

    for idx, vid_file in enumerate(iterator, 1):
        if not use_tqdm:
            _emit(f"[{idx}/{len(video_files)}] Muting: {vid_file.name}", log_func)
        try:
            if output_path:
                rel = vid_file.relative_to(input_dir) if recursive else Path(vid_file.name)
                stem = rel.stem
                save_dir = output_path / rel.parent
                save_dir.mkdir(parents=True, exist_ok=True)
            else:
                stem = vid_file.stem
                save_dir = vid_file.parent

            if overwrite:
                save_path = save_dir / (stem + ".mp4")
            else:
                save_path = save_dir / (stem + "_muted.mp4")

            if dry_run:
                _emit(f"[DRY] mute audio {vid_file} -> {save_path}", log_func)
                dels = delete_exts or []
                orig_ext = vid_file.suffix.lower().lstrip('.')
                if orig_ext in dels:
                    _emit(f"[DRY] would delete original {vid_file.name}", log_func)
                processed += 1
                continue

            cmd = ["ffmpeg", "-y", "-i", str(vid_file), "-c:v", "copy", "-an", str(save_path)]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
            if r.returncode == 0:
                processed += 1
                _emit(f"Muted: {vid_file.name} -> {save_path.name}", log_func)

                dels = delete_exts or []
                if not dry_run and dels:
                    orig_ext = vid_file.suffix.lower().lstrip('.')
                    if orig_ext in dels:
                        try:
                            vid_file.unlink()
                            _emit(f"Deleted original: {vid_file.name}", log_func)
                        except Exception as e:
                            _emit(f"Failed delete {vid_file.name}: {e}", log_func)
            else:
                _emit(f"Mute failed: {r.stderr[-200:]}", log_func)
                errors += 1
        except Exception as e:
            _emit(f"Mute error: {e}", log_func)
            errors += 1

    return processed, errors


def main() -> None:
    # Support launching the simple GUI from the same script
    if "--gui" in sys.argv or "-g" in sys.argv:
        try:
            from image_batch_gui import run_gui
            run_gui()
            return
        except ImportError:
            print("GUI module not found. Make sure image_batch_gui.py is in the same directory.")
            sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Complete Image+Video Batch Processor: strip meta + rename + compress + resize + trim + mute. All-in-one powerful tool.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog="""
Examples:

  # Strip + rename (existing)
  python image_batch.py --dir ./media --action strip_meta --recursive --dry-run
  python image_batch.py --dir ./media --action rename --pattern "IMG_" --new-prefix "photo_" -r

  # Image compression to WebP (quality/speed balance like media_compressor)
  python image_batch.py --dir ./photos --action compress --quality 78 --recursive --dry-run

  # Video compression (CRF + preset, good balance)
  python image_batch.py --dir ./videos --action compress-video --crf 28 --preset medium --recursive

  # Resize images or videos
  python image_batch.py --dir ./media --action resize --width 1920 --recursive --dry-run
  python image_batch.py --dir ./media --action resize --scale 0.5

  # Video trim + mute
  python image_batch.py --dir ./videos --action trim --start 00:01:30 --end 00:05:00
  python image_batch.py --dir ./videos --action mute --recursive

  # Full power (run multiple times or combine in scripts): strip -> rename -> compress

See README for full details and GUI.
""",
    )
    parser.add_argument(
        "--dir",
        required=True,
        help="Target root directory containing the images to process",
    )
    parser.add_argument(
        "--action",
        choices=["strip_meta", "rename", "compress", "compress-video", "resize", "trim", "mute"],
        required=True,
        help="Operation to perform (new: compress, compress-video, resize, trim, mute)",
    )
    parser.add_argument(
        "--output",
        help="Destination directory for processed files (strip_meta only). "
             "Directory tree is preserved when used together with --recursive.",
    )
    parser.add_argument(
        "--pattern",
        help="Substring that must appear in the filename (case-insensitive) for rename mode",
    )
    parser.add_argument(
        "--new-prefix",
        help="Prefix to use for renamed files, e.g. 'holiday_' or 'img_'",
    )
    parser.add_argument(
        "--start-num",
        type=int,
        default=1,
        help="Starting number for sequential rename (will be zero-padded to 3 digits)",
    )
    parser.add_argument(
        "-r", "--recursive",
        action="store_true",
        help="Recurse into all subdirectories",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing anything to disk",
    )

    # New feature args
    parser.add_argument("--quality", type=int, default=80, help="Image WebP quality (0-100). 78=good balance from media_compressor")
    parser.add_argument("--target-size", type=int, help="Target file size in bytes for image compression (searches best quality)")
    parser.add_argument("--crf", type=int, default=28, help="Video x265 CRF (lower=better quality, larger file). 23-32 range")
    parser.add_argument("--preset", default="medium", help="ffmpeg preset (ultrafast..veryslow). medium/slow = good quality/speed")
    parser.add_argument("--width", type=int, help="Resize target width (keeps aspect if height omitted)")
    parser.add_argument("--height", type=int, help="Resize target height")
    parser.add_argument("--scale", type=float, help="Resize scale factor e.g. 0.5 for half size")
    parser.add_argument("--start", help="Video trim start time (e.g. 00:01:30 or 90)")
    parser.add_argument("--end", help="Video trim end time")
    parser.add_argument("--mute", action="store_true", help="Mute audio when compressing/processing video")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite in place (same stem) for compress/resize etc instead of _suffix (default False)")
    parser.add_argument("--delete-ext", help="Comma sep exts to delete originals after success, e.g. jpg,jpeg (simulated in dry-run)")

    args = parser.parse_args()

    # Parse common new flags (used by compress/resize/trim/mute; ignored by strip/rename)
    overwrite = bool(getattr(args, "overwrite", False))
    delete_exts = []
    if getattr(args, "delete_ext", None):
        delete_exts = [
            e.strip().lower().lstrip(".") for e in args.delete_ext.split(",") if e.strip()
        ]

    if args.action == "strip_meta":
        processed, errors = strip_metadata(
            args.dir, args.output, args.recursive, args.dry_run
        )
        print("\n=== strip_meta finished ===")
        print(f"Successfully processed: {processed}")
        print(f"Errors / skipped:       {errors}")
        if args.dry_run:
            print("DRY RUN - no files were actually modified.")

    elif args.action == "rename":
        if not args.pattern or not args.new_prefix:
            print("ERROR: Both --pattern and --new-prefix are required when action=rename.")
            parser.print_help()
            sys.exit(1)

        processed, skipped = batch_rename(
            args.dir, args.pattern, args.new_prefix, args.start_num, args.recursive, args.dry_run
        )
        print("\n=== rename finished ===")
        print(f"Successfully renamed: {processed}")
        print(f"Skipped / errors:     {skipped}")
        if args.dry_run:
            print("DRY RUN - no files were actually modified.")

    elif args.action == "compress":
        target_size = getattr(args, "target_size", None)
        processed, errors = compress_image(
            args.dir, args.quality, target_size, args.output, args.recursive, args.dry_run, overwrite, delete_exts
        )
        ts = f" target_size={target_size}" if target_size else ""
        print(f"\n=== compress (images->WebP quality={args.quality}{ts}) finished ===")
        print(f"Processed: {processed}  Errors: {errors}")

    elif args.action == "compress-video":
        processed, errors = compress_video(
            args.dir, args.crf, args.preset, args.output, args.recursive, args.dry_run, args.mute, overwrite, delete_exts
        )
        print(f"\n=== compress-video (CRF={args.crf} preset={args.preset}) finished ===")
        print(f"Processed: {processed}  Errors: {errors}")

    elif args.action == "resize":
        processed, errors = resize_media(
            args.dir, args.width, args.height, args.scale, args.output, args.recursive, args.dry_run, overwrite, delete_exts
        )
        print(f"\n=== resize finished ===")
        print(f"Processed: {processed}  Errors: {errors}")

    elif args.action == "trim":
        if not args.start and not args.end:
            print("ERROR: --start and/or --end required for --action trim")
            sys.exit(1)
        processed, errors = trim_video(
            args.dir, args.start, args.end, args.output, args.recursive, args.dry_run, overwrite, delete_exts
        )
        print(f"\n=== trim finished ===")
        print(f"Processed: {processed}  Errors: {errors}")

    elif args.action == "mute":
        processed, errors = mute_video(
            args.dir, args.output, args.recursive, args.dry_run, overwrite, delete_exts
        )
        print(f"\n=== mute audio finished ===")
        print(f"Processed: {processed}  Errors: {errors}")


if __name__ == "__main__":
    main()
