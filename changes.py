"""Human-readable diff between the initial project snapshot and its delivery."""
import difflib
import hashlib
from pathlib import Path


def inventory(folder):
    return {p.relative_to(folder) for p in folder.rglob("*") if p.is_file()}


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").digest()


def lines(path):
    if not path.is_file():
        return []
    if path.stat().st_size > 1024 * 1024:
        return None
    raw = path.read_bytes()
    if b"\x00" in raw:
        return None
    try:
        return raw.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError:
        return None


def write_diff(job):
    base, result = job.path / "baseline", job.path / "project"
    if not result.is_dir():
        raise ValueError("交付目录尚未生成")
    report = job.path / "changes.txt"
    changes = 0
    with report.open("w", encoding="utf-8-sig") as handle:
        for name in sorted(inventory(base) | inventory(result)):
            before, after = base / name, result / name
            if before.exists() and after.exists() and digest(before) == digest(after):
                continue
            changes += 1
            kind = "新增" if not before.exists() else "删除" if not after.exists() else "修改"
            handle.write(f"[{kind}] {name.as_posix()}\n")
            a, b = lines(before), lines(after)
            if a is None or b is None:
                handle.write("二进制或超过 1 MiB 的文件，仅列出路径。\n")
            else:
                for line in difflib.unified_diff(a, b, "初始文件", "交付文件", lineterm=""):
                    handle.write(line + "\n")
            handle.write("\n")
        handle.write(f"共 {changes} 个文件有改动。\n")
    return report
