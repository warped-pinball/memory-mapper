# Contributing to Memory Mapper

Thanks for your interest in improving Memory Mapper! This document covers the
technical details you'll need to develop, test, and understand the tool.

## Development setup

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run the test suite
pytest -v
```

The package lives in `memory_mapper/`:

- `receiver.py` — joins the UDP multicast group and receives datagrams.
- `tracker.py` — tracks per-byte change history and drives the scan/filter logic.
- `display.py` — renders the full-screen terminal UI (memory panel, cursor
  inspector, legend, and menu).
- `__main__.py` — CLI entry point and argument parsing.

## Message format

Memory Mapper is a passive listener. It expects raw UDP multicast datagrams
whose payload is the full memory snapshot as a contiguous byte array — for
example 256 or 4096 bytes of memory. There is no header or framing: the entire
datagram payload is the snapshot.

Each packet completely replaces the previous snapshot for that sender, and any
byte offsets whose values differ from the previous snapshot are highlighted
automatically. By default the tool listens on multicast group `239.255.0.0`,
port `2040`; both are configurable via `--group` and `--port`.

## Pre-built binaries and releases

Publishing a GitHub Release (a tag matching `v*`) triggers the
[Build & Release](.github/workflows/build.yml) workflow, which uses PyInstaller
to produce standalone executables for Linux (x86_64 `.deb`), Raspberry Pi
(ARM64 `.deb`), macOS, and Windows.

Each platform artifact is uploaded once, under a versioned name (e.g.
`warped-pinball-memory-mapper-linux-amd64-1.2.0.deb`), so every asset on a
release is unambiguous about which version it contains.

Because the assets are versioned, there are no fixed
`releases/latest/download/<name>` URLs; `README.md` and `USER_GUIDE.md` link to
the `releases/latest` page instead and describe the naming scheme.

### Pull request previews

Every pull request triggers the [PR Build](.github/workflows/pr-build.yml)
workflow, which builds the same set of installers from the PR's HEAD commit. A
companion [PR Build Comment](.github/workflows/pr-build-comment.yml) workflow
posts (and keeps updated) a sticky comment on the pull request with download
links for each platform, so reviewers can install and test the proposed change
without checking out the branch locally. The comment is refreshed automatically
every time a new commit is pushed to the PR.

## Versioning

The version is declared in exactly one place — `memory_mapper/__init__.py`
(`__version__`). `pyproject.toml` reads it dynamically
(`[tool.setuptools.dynamic]`), so the two can never drift.

To cut a release:

1. Bump `__version__` in `memory_mapper/__init__.py`.
2. Create a matching `v<version>` tag / GitHub Release.

The [Version Check](.github/workflows/version-check.yml) workflow fails the
release if the tag doesn't match `__version__`, so a mismatch can't ship. It
also runs on pull requests that touch the version so drift is caught early.
