#!/usr/bin/env python3
"""Grade a cleaned fixture home built by build_fixture.py.

Usage: python3 grade.py HOME
Prints PASS/FAIL per check and exits 1 if any check fails.
"""
import subprocess
import sys
from pathlib import Path

from build_fixture import MARKERS

MUST_KEEP = [
    ".bashrc", ".ssh/id_ed25519", "Documents/report.pdf", "Downloads/essay.docx",
    ".lmstudio/conversations/chat.json", ".config/google-chrome/Default/Bookmarks",
    ".config/Code/User/settings.json", ".local/share/Trash/files/vacation.jpg",
    "projects/analysis/analysis.py", "projects/analysis/data/results.csv",
    "projects/analysis/notebook.ipynb", "projects/analysis/ext/fast.c",
    "projects/myapp/README.md", "projects/myapp/assets/logo.png",
    "projects/myapp/android/gradle/wrapper/gradle-wrapper.properties",
    "projects/myapp/notes.md", "Documents/school-project-backup.zip", ".gitconfig",
]

MUST_GO = [
    ".zcompdump", "anaconda3", ".conda", ".nvm", ".npm", ".cache", ".gradle", "Android", ".android",
    ".lmstudio/models", ".config/google-chrome/Default/Cache",
    ".config/google-chrome/Default/Service Worker", ".config/google-chrome/optimization_guide_model_store",
    ".config/Code/CachedData", ".vscode/extensions",
    "Downloads/Tool-1.0-x86_64.AppImage", "Downloads/ide-2025.1.tar.gz", "Downloads/ide-2025.1",
    "Downloads/Miniconda3-latest-Linux-x86_64.sh",
    "projects/analysis/.venv", "projects/analysis/__pycache__", "projects/analysis/.ipynb_checkpoints",
    "projects/analysis/ext/fast.o", "projects/analysis/ext/fast.so",
    "projects/myapp/node_modules", "projects/myapp/build", "projects/myapp/dist", "projects/myapp/.idea",
    "projects/myapp/android/.gradle", "projects/myapp/android/app/build",
    "projects/myapp/android/gradle/wrapper/gradle-wrapper.jar",
    ".local/share/Trash/files/myapp.2/node_modules",
    "projects/upstream-lib",
    "Downloads/videoplayback.mp4", "Downloads/doi_10_5061_dryad_abc123__v20250101.zip",
    ".config/google-chrome/Default/IndexedDB", ".config/google-chrome/Default/File System",
    ".local/share/applications/jetbrains-ide.desktop",
    ".local/share/Trash/files/myapp.2", "Downloads/myapp-main", "projects/myapp/.git",
]


def marker_survives(home: Path, marker: str) -> bool:
    """True if the marker is in a plain file, or in the history/worktree of a surviving git repo."""
    if subprocess.run(["grep", "-rqF", "--exclude-dir=.git", marker, str(home)]).returncode == 0:
        return True
    for gitdir in home.rglob(".git"):
        repo = gitdir.parent
        log = subprocess.run(["git", "-C", str(repo), "log", "--all", "-p"], capture_output=True, text=True)
        if marker in log.stdout:
            return True
    return False


def manifest_records(home: Path, pkg: str, ver: str) -> bool:
    """True if some surviving plain file names the package and its version (a rebuild manifest)."""
    for f in home.rglob("*"):
        if f.is_file() and ".git" not in f.parts and f.stat().st_size < 1_000_000:
            text = f.read_text(errors="ignore")
            if pkg in text and ver in text:
                return True
    return False


def main(home: Path) -> int:
    results = []
    for rel in MUST_KEEP:
        results.append((f"keep {rel}", (home / rel).exists()))
    for rel in MUST_GO:
        results.append((f"gone {rel}", not (home / rel).exists() and not (home / rel).is_symlink()))
    for name, marker in MARKERS.items():
        results.append((f"rescued {name} work", marker_survives(home, marker)))
    for pkg, ver in (("numpy", "1.26.4"), ("pandas", "2.2.0")):
        results.append((f"env manifest records {pkg} {ver}", manifest_records(home, pkg, ver)))
    photos = [p for p in ("Downloads/photo.jpg", "Downloads/photo (1).jpg") if (home / p).exists()]
    results.append(("exactly one copy of duplicate photo", len(photos) == 1))
    poster = [p for p in ("Downloads/poster.zip", "Downloads/poster") if (home / p).exists()]
    results.append(("exactly one of poster.zip / extracted poster/", len(poster) == 1))

    for name, ok in results:
        print(("PASS " if ok else "FAIL ") + name)
    failed = sum(not ok for _, ok in results)
    print(f"\n{len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
