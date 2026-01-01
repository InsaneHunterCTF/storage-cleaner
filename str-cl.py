#!/usr/bin/env python3
"""
str-cl.py (storage_cleaner) — storage scanner & cleaner for local filesystem and Android devices (via adb).

This version:
 - saves last phone scan to ~/.str_cl_last_scan.json
 - allows deleting specific device files by index or path
 - clean-phone accepts positional device paths (or scans if none passed)
 - delete-phone selects by index or path with safe confirmation
"""

from __future__ import annotations
import os
import sys
import json
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import List, Tuple, Optional

import click
from humanfriendly import format_size, parse_size
from send2trash import send2trash

LAST_SCAN_FILE = Path.home() / ".str_cl_last_scan.json"


# Helpers

def human(n: int) -> str:
    return format_size(n, binary=True)


def walk_path_collect(path: Path, min_size: int, extensions: Optional[List[str]], exclude_dirs: List[str]) -> List[Tuple[int, str]]:
    results: List[Tuple[int, str]] = []
    stack = [path]
    while stack:
        p = stack.pop()
        try:
            with os.scandir(p) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_file():
                            try:
                                st = entry.stat(follow_symlinks=False)
                                size = st.st_size
                            except Exception:
                                continue
                            if size >= min_size:
                                if extensions:
                                    if any(entry.name.lower().endswith(ext.lower()) for ext in extensions):
                                        results.append((size, str(Path(p) / entry.name)))
                                else:
                                    results.append((size, str(Path(p) / entry.name)))
                        elif entry.is_dir():
                            if entry.name in exclude_dirs:
                                continue
                            stack.append(Path(p) / entry.name)
                    except PermissionError:
                        continue
        except PermissionError:
            continue
        except FileNotFoundError:
            continue
    return results


def top_n_sorted(files: List[Tuple[int, str]], n: int) -> List[Tuple[int, str]]:
    return sorted(files, key=lambda x: x[0], reverse=True)[:n]



# Android (adb) helpers

def check_adb() -> bool:
    return shutil.which("adb") is not None


def adb_shell(cmd: str, timeout: int = 90) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(["adb", "shell", cmd], capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:
        return 1, "", str(e)


def _parse_ls_line(line: str, current_dir: str) -> Optional[Tuple[int, str]]:
    if not line:
        return None
    line = line.rstrip()
    if line.startswith("total "):
        return None
    parts = line.split()
    if len(parts) < 6:
        return None
    size_token = None
    name_token = None
    if len(parts) > 4 and parts[4].isdigit():
        size_token = parts[4]
        name_token = " ".join(parts[8:]) if len(parts) > 8 else parts[-1]
    else:
        for i, tk in enumerate(parts):
            if tk.isdigit():
                size_token = tk
                name_token = " ".join(parts[i + 1 :]) if i + 1 < len(parts) else None
                break
    if not size_token or not name_token:
        return None
    try:
        sz = int(size_token)
    except Exception:
        return None
    name = name_token
    if name.startswith("/"):
        full = name
    else:
        full = current_dir.rstrip("/") + "/" + name.lstrip("/")
    return (sz, full)


def _parse_ls_lR_output(output: str, assumed_root: str) -> List[Tuple[int, str]]:
    files: List[Tuple[int, str]] = []
    current_dir = assumed_root.rstrip("/")
    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line.endswith(":") and (line.startswith("/") or line.startswith(assumed_root)):
            current_dir = line[:-1]
            continue
        parsed = _parse_ls_line(line, current_dir)
        if parsed:
            files.append(parsed)
            continue
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit() and parts[1].startswith("/"):
            try:
                files.append((int(parts[0]), parts[1].strip()))
            except Exception:
                continue
    return files


def adb_list_files(root: str = "/sdcard", debug: bool = False) -> List[Tuple[int, str]]:
    if not check_adb():
        raise RuntimeError("adb not found on PATH")

    roots_to_try = [root, "/storage/emulated/0", "/sdcard"]
    tried_roots = set()
    results: List[Tuple[int, str]] = []

    for candidate_root in roots_to_try:
        if candidate_root in tried_roots:
            continue
        tried_roots.add(candidate_root)

        # 1) find -ls

        cmd = f"find {shlex.quote(candidate_root)} -type f -ls"
        if debug:
            click.echo(f"[debug] adb: {cmd}")
        rc, out, err = adb_shell(cmd, timeout=120)
        if rc == 0 and out.strip():
            parsed: List[Tuple[int, str]] = []
            for line in out.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split(None, 1)
                if len(parts) == 2 and parts[0].isdigit() and parts[1].startswith("/"):
                    try:
                        parsed.append((int(parts[0]), parts[1].strip()))
                    except Exception:
                        continue
                else:
                    tokens = line.split()
                    found = False
                    for i, tk in enumerate(tokens):
                        if tk.isdigit():
                            rest = " ".join(tokens[i + 1 :])
                            if rest.startswith("/"):
                                try:
                                    parsed.append((int(tk), rest))
                                    found = True
                                except Exception:
                                    pass
                                break
                    if found:
                        continue
            if parsed:
                if debug:
                    click.echo(f"[debug] parsed {len(parsed)} entries from find -ls")
                return parsed

        # 2) find -exec stat

        cmd = f"find {shlex.quote(candidate_root)} -type f -exec stat -c '%s %n' {{}} \\;"
        if debug:
            click.echo(f"[debug] adb: {cmd}")
        rc, out, err = adb_shell(cmd, timeout=120)
        if rc == 0 and out.strip():
            parsed = []
            for line in out.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split(None, 1)
                if len(parts) == 2 and parts[0].isdigit() and parts[1].startswith("/"):
                    try:
                        parsed.append((int(parts[0]), parts[1].strip()))
                    except Exception:
                        continue
            if parsed:
                if debug:
                    click.echo(f"[debug] parsed {len(parsed)} entries from find -exec stat")
                return parsed

        # 3) ls -lR

        cmd = f"ls -lR {shlex.quote(candidate_root)}"
        if debug:
            click.echo(f"[debug] adb: {cmd}")
        rc, out, err = adb_shell(cmd, timeout=120)
        if rc == 0 and out.strip():
            parsed = _parse_ls_lR_output(out, candidate_root)
            if parsed:
                if debug:
                    click.echo(f"[debug] parsed {len(parsed)} entries from ls -lR")
                return parsed

        # 4) fallback iterative per-dir approach

        cmd = f"ls -l {shlex.quote(candidate_root)}"
        if debug:
            click.echo(f"[debug] adb: {cmd}")
        rc, out, err = adb_shell(cmd, timeout=60)
        if rc != 0 or not out.strip():
            if debug:
                click.echo(f"[debug] ls -l returned empty or failed (rc={rc})")
            continue

        files_here: List[Tuple[int, str]] = []
        subdirs: List[str] = []
        current_dir = candidate_root.rstrip("/")
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            parsed = _parse_ls_line(line, current_dir)
            if parsed:
                files_here.append(parsed)
                continue
            if line.startswith("d"):
                parts = line.split()
                if parts:
                    name = parts[-1]
                    if name and not name.startswith("/"):
                        subdirs.append(current_dir.rstrip("/") + "/" + name)
        results.extend(files_here)

        for sub in subdirs:
            time.sleep(0.02)
            cmd = f"ls -l {shlex.quote(sub)}"
            if debug:
                click.echo(f"[debug] adb: {cmd}")
            rc2, out2, err2 = adb_shell(cmd, timeout=30)
            if rc2 != 0 or not out2.strip():
                cmd2 = f"ls -lR {shlex.quote(sub)}"
                if debug:
                    click.echo(f"[debug] adb fallback: {cmd2}")
                rc3, out3, err3 = adb_shell(cmd2, timeout=60)
                if rc3 == 0 and out3.strip():
                    parsed_sub = _parse_ls_lR_output(out3, sub)
                    if parsed_sub:
                        results.extend(parsed_sub)
                        continue
                    else:
                        continue
                else:
                    continue
            for line in out2.splitlines():
                parsed = _parse_ls_line(line, sub)
                if parsed:
                    results.append(parsed)

        if results:
            if debug:
                click.echo(f"[debug] aggregated {len(results)} entries via iterative scan")
            return results

    return []


def adb_stat_size(path: str) -> Optional[int]:
    # Try stat -c %s "path"

    rc, out, err = adb_shell(f"stat -c %s {shlex.quote(path)}", timeout=15)
    if rc == 0 and out.strip().isdigit():
        try:
            return int(out.strip())
        except Exception:
            pass
    # Fallback to ls -l path and parse

    rc, out, err = adb_shell(f"ls -l {shlex.quote(path)}", timeout=10)
    if rc == 0 and out.strip():
        # parse first file line

        for line in out.splitlines():
            parsed = _parse_ls_line(line.strip(), os.path.dirname(path) or "/")
            if parsed:
                return parsed[0]
    return None


def adb_delete(path: str) -> Tuple[bool, str]:
    if not check_adb():
        return False, "adb not found"
    try:
        cmd = ["adb", "shell", "rm", "-f", shlex.quote(path)]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if p.returncode == 0:
            return True, p.stdout.strip()
        else:
            return False, p.stderr.strip() or p.stdout.strip()
    except Exception as e:
        return False, str(e)



# CLI tool itself (click)

@click.group()
def cli():
    pass


@cli.command()
@click.argument("paths", nargs=-1, type=click.Path(exists=True, file_okay=True, dir_okay=True))
@click.option("--min-size", default="100M", help="Minimum file size to report (e.g. 100M, 2G).")
@click.option("--extensions", default="", help="Comma separated extensions to include (e.g. .mp4,.zip).")
@click.option("--exclude-dirs", default="", help="Comma separated directory names to exclude (e.g. .cache,node_modules).")
@click.option("--top", default=20, help="Show top N largest files.")
@click.option("--json", "to_json", default="", help="Write report to JSON file path.")
def scan(paths, min_size, extensions, exclude_dirs, top, to_json):
    if not paths:
        paths = (os.path.expanduser("~"),)
    min_size_bytes = parse_size(min_size)
    exts = [e.strip() for e in extensions.split(",") if e.strip()]
    excludes = [e.strip() for e in exclude_dirs.split(",") if e.strip()]
    all_results: List[Tuple[int, str]] = []
    click.echo(f"Scanning paths: {', '.join(paths)} (min size {human(min_size_bytes)})")
    for p in paths:
        pth = Path(p).expanduser()
        if pth.is_file():
            try:
                sz = pth.stat().st_size
            except Exception:
                continue
            if sz >= min_size_bytes:
                if exts:
                    if any(pth.name.lower().endswith(x.lower()) for x in exts):
                        all_results.append((sz, str(pth)))
                else:
                    all_results.append((sz, str(pth)))
        else:
            res = walk_path_collect(pth, min_size_bytes, exts or None, excludes)
            all_results.extend(res)

    top_files = top_n_sorted(all_results, top)
    if not top_files:
        click.secho("No files found matching criteria.", fg="yellow")
        return

    click.secho(f"Top {len(top_files)} files:", fg="green")
    for size, path in top_files:
        click.echo(f"{human(size):>10}    {path}")

    if to_json:
        report = [{"size": s, "path": p} for s, p in all_results]
        with open(to_json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        click.secho(f"Saved report to {to_json}", fg="blue")

# Commands, I ensured that they contain everything the user would need

@cli.command()
@click.argument("paths", nargs=-1, type=str)
@click.option("--min-size", default="100M", help="Minimum file size to consider for deletion.")
@click.option("--extensions", default="", help="Comma separated extensions to include.")
@click.option("--top", default=50, help="Consider only top N files for deletion.")
@click.option("--yes", is_flag=True, default=False, help="Do not prompt for deletion.")
@click.option("--permanent", is_flag=True, default=False, help="Permanently delete files (device deletions are always permanent).")
@click.option("--dry-run", is_flag=True, default=False, help="Show what would be deleted but don't delete.")
@click.option("--debug", is_flag=True, default=False, help="Print debug adb parsing info.")
def clean_phone(paths, min_size, extensions, top, yes, permanent, dry_run, debug):
    """
    If 'paths' are provided as positional arguments, delete those device paths (no scan).
    Otherwise scan device root (see --root in scan-phone) and present candidates.
    """
    if not check_adb():
        click.secho("adb not found on PATH. Install Android platform-tools.", fg="red")
        sys.exit(1)

    # If explicit paths provided, get their sizes and treat them as candidates

    exts = [e.strip() for e in extensions.split(",") if e.strip()]
    candidates: List[Tuple[int, str]] = []

    if paths:
        for p in paths:
            size = adb_stat_size(p)
            if size is None:
                click.secho(f"Could not stat {p}; skipping.", fg="yellow")
                continue
            if exts and not any(p.lower().endswith(x.lower()) for x in exts):
                continue
            candidates.append((size, p))
    else:
        # no explicit paths — do a scan (use same defaults as scan_phone)
        files = adb_list_files("/storage/emulated/0", debug=debug)
        min_bytes = parse_size(min_size)
        if exts:
            files = [f for f in files if any(f[1].lower().endswith(x.lower()) for x in exts)]
        candidates = [f for f in files if f[0] >= min_bytes]

    candidates = top_n_sorted(candidates, top)
    if not candidates:
        click.secho("No candidates to delete on device.", fg="yellow")
        return

    click.secho("Device deletion candidates (PERMANENT):", fg="red")
    for i, (size, path) in enumerate(candidates, 1):
        click.echo(f"[{i}] {human(size):>10}    {path}")

    if dry_run:
        click.secho("Dry-run: no files will be removed.", fg="yellow")
        return

    if not yes:
        ans = click.prompt("This will PERMANENTLY delete the listed files on device. Type 'DELETE' to confirm", default="", show_default=False)
        if ans != "DELETE":
            click.secho("Aborted by user.", fg="yellow")
            return

    # perform deletion

    for size, path in candidates:
        ok, out = adb_delete(path)
        if ok:
            click.secho(f"Deleted: {path}", fg="green")
        else:
            click.secho(f"Failed to delete {path}: {out}", fg="yellow")


@cli.command()
@click.option("--root", default="/sdcard", help="Root path on device to scan (default /sdcard).")
@click.option("--min-size", default="50M", help="Minimum file size to report on device.")
@click.option("--extensions", default="", help="Comma separated extensions to include.")
@click.option("--top", default=50, help="Show top N files on device.")
@click.option("--json", "to_json", default="", help="Write report to JSON file path.")
@click.option("--debug", is_flag=True, default=False, help="Print debug adb parsing info.")
def scan_phone(root, min_size, extensions, top, to_json, debug):
    """Scan connected Android device for large files (requires adb)."""
    if not check_adb():
        click.secho("adb not found on PATH. Install Android platform-tools.", fg="red")
        sys.exit(1)

    min_bytes = parse_size(min_size)
    exts = [e.strip() for e in extensions.split(",") if e.strip()]

    click.secho("Listing files on device (this may take a while)...", fg="blue")
    files = adb_list_files(root, debug=debug)
    # Filter
    files = [f for f in files if f[0] >= min_bytes]
    if exts:
        files = [f for f in files if any(f[1].lower().endswith(x.lower()) for x in exts)]

    top_files = top_n_sorted(files, top)
    if not top_files:
        click.secho("No large files found on device.", fg="yellow")
        return

    # Print and save last scan to ~/.str_cl_last_scan.json for later delete-by-index (path selected above)
    click.secho(f"Top {len(top_files)} files on device:", fg="green")
    scan_entries = []
    for i, (size, path) in enumerate(top_files, 1):
        click.echo(f"[{i}] {human(size):>10}    {path}")
        scan_entries.append({"index": i, "size": size, "path": path})

    try:
        with open(LAST_SCAN_FILE, "w", encoding="utf-8") as f:
            json.dump(scan_entries, f, indent=2)
        click.secho(f"Saved last scan to {LAST_SCAN_FILE}", fg="blue")
    except Exception:
        pass

    if to_json:
        with open(to_json, "w", encoding="utf-8") as f:
            json.dump([{"size": s, "path": p} for s, p in files], f, indent=2)
        click.secho(f"Saved device report to {to_json}", fg="blue")


@cli.command(name="delete-phone")
@click.option("--index", "indices", multiple=True, help="Index from last scan to delete (can be multiple). Example: --index 2 --index 5")
@click.option("--path", "paths", multiple=True, help="Exact device path to delete (can be multiple).")
@click.option("--from-scan", is_flag=True, default=False, help="Use the last saved scan (~/.str_cl_last_scan.json) to select items.")
@click.option("--yes", is_flag=True, default=False, help="Do not prompt; perform deletion.")
@click.option("--dry-run", is_flag=True, default=False, help="Show what would be deleted but don't delete.")
def delete_phone(indices, paths, from_scan, yes, dry_run):
    """Delete specific file(s) on device by index (from last scan) or by explicit path."""
    if not check_adb():
        click.secho("adb not found on PATH. Install Android platform-tools.", fg="red")
        sys.exit(1)

    # Build a candidate list (size, path)
    candidates: List[Tuple[int, str]] = []

    # Load last scan if requested or indices provided
    scan_entries = []
    if from_scan or indices:
        if LAST_SCAN_FILE.exists():
            try:
                scan_entries = json.loads(LAST_SCAN_FILE.read_text(encoding="utf-8"))
            except Exception:
                scan_entries = []
        else:
            click.secho(f"No last scan found at {LAST_SCAN_FILE}. Run scan-phone first.", fg="yellow")
            scan_entries = []

    # If indices passed as comma strings (click allows multiple), flatten them
    flat_indices: List[int] = []
    for idx in indices:
        # allow comma-separated inside a single --index value
        for token in str(idx).split(","):
            token = token.strip()
            if not token:
                continue
            try:
                flat_indices.append(int(token))
            except Exception:
                pass

    # Add by index
    if flat_indices and scan_entries:
        for i in flat_indices:
            entry = next((e for e in scan_entries if e.get("index") == i), None)
            if entry:
                candidates.append((entry.get("size", 0), entry.get("path")))
            else:
                click.secho(f"Index {i} not found in last scan; skipping.", fg="yellow")

    # Add by paths provided explicitly
    for p in paths:
        size = adb_stat_size(p)
        if size is None:
            click.secho(f"Could not stat {p}; skipping.", fg="yellow")
            continue
        candidates.append((size, p))

    if not candidates:
        click.secho("No files selected for deletion.", fg="yellow")
        return

    # Show summary
    click.secho("Selected files for deletion (PERMANENT):", fg="red")
    for i, (sz, path) in enumerate(candidates, 1):
        click.echo(f"[{i}] {human(sz):>10}    {path}")

    if dry_run:
        click.secho("Dry-run: no files will be removed.", fg="yellow")
        return

    if not yes:
        ans = click.prompt("This will PERMANENTLY delete the listed files on device. Type 'DELETE' to confirm", default="", show_default=False)
        if ans != "DELETE": # Confirmation for deletion of the selected file
            click.secho("Aborted by user.", fg="yellow")
            return

    # Delete
    for sz, path in candidates:
        ok, out = adb_delete(path)
        if ok:
            click.secho(f"Deleted: {path}", fg="green")
        else:
            click.secho(f"Failed to delete {path}: {out}", fg="yellow")


if __name__ == "__main__":
    cli()
