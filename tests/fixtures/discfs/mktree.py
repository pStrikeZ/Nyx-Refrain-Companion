#!/usr/bin/env python3
"""Writes the fixture tree. Every file's content is a function of its size alone
(sha256(str(size)) repeated), and every size is unique, so a test can check any file
whatever its name was turned into."""
import hashlib, os, sys

def content(size):
    block = hashlib.sha256(str(size).encode()).digest()
    return (block * (size // 32 + 1))[:size]

FILES = {
    "01 first.mp3": 5000,
    "tiny.flac": 10,
    "empty.ogg": 0,
    "readme.txt": 123,
    "アルバム A/01 曲目.mp3": 70000,
    "アルバム A/02 曲目.m4a": 70001,
    "アルバム A/cover.jpg": 3000,
    "deep/a/b/c/track.opus": 4096,
    "B/a long file name with spaces and more than thirty-one characters, and then some more to pass sixty-four.flac": 50000,
}
FILES.update({f"many/f{i:03d}.mp3": 400 + i for i in range(300)})

if __name__ == "__main__":
    root = sys.argv[1]
    for path, size in FILES.items():
        full = os.path.join(root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(content(size))
