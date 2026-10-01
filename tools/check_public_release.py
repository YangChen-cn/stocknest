"""Scan tracked files and every reachable Git blob; print filenames, never contents."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{30,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"AIza[A-Za-z0-9_-]{35}"),
    re.compile(rb"AKIA[A-Z0-9]{16}"),
    re.compile(rb"/Users/[A-Za-z0-9_.-]+/"),
    re.compile(rb"(?:GMAIL_APP_PASSWORD|GITHUB_TOKEN)\s*[:=]\s*['\"][A-Za-z0-9]{16,}['\"]"),
]
PROHIBITED = (".stockwatch/", "data/", "logs/", "outputs/", ".venv/", ".streamlit/", ".cache/")


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], stderr=subprocess.DEVNULL)


def scan(deny_terms=()):
    patterns = PATTERNS + [re.compile(re.escape(term.encode()), re.IGNORECASE) for term in deny_terms]
    failures = []
    names = git("ls-files", "-z").decode().split("\0")
    for name in filter(None, names):
        if name in ("config.yaml", "config.local.yaml", ".env") or name.startswith(PROHIBITED) or name.startswith(".env."):
            failures.append("Private tracked path: " + name)
        raw = (ROOT / name).read_bytes()
        if any(pattern.search(raw) for pattern in patterns):
            failures.append("Sensitive pattern in current file: " + name)
    objects = git("rev-list", "--objects", "--all").decode().splitlines()
    count = 0
    for item in objects:
        sha, _, name = item.partition(" ")
        if git("cat-file", "-t", sha).strip() != b"blob":
            continue
        count += 1
        raw = git("cat-file", "blob", sha)
        if any(pattern.search(raw) for pattern in patterns):
            failures.append("Sensitive pattern in history: " + (name or sha))
        if name in ("config.yaml", ".env") or name.startswith(PROHIBITED):
            failures.append("Private historical path: " + name)
    if failures:
        print("\n".join(sorted(set(failures))))
        return 1
    print(f"PASS: {len([n for n in names if n])} tracked files; {count} reachable blobs; no prohibited paths or secret/path patterns")
    print("This is a heuristic check; manually inspect demo data, screenshots and commit metadata too.")
    return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--deny", action="append", default=[], help="Additional known private identifier; output never prints matching contents")
    args = parser.parse_args()
    raise SystemExit(scan(args.deny))
