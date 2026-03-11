# memory-mapper

A cross-platform CLI tool that listens for UDP multicast memory snapshots and displays them in the terminal, **highlighting recently-changed bytes** with a yellow background.

---

## Quick start

```bash
pip install .
memory-mapper
```

By default the tool joins multicast group `239.255.0.0` on port `2040`.  
Press **Ctrl-C** to exit.

---

## Usage

```
usage: memory-mapper [-h] [--version] [--group GROUP] [--port PORT]
                     [--highlight-duration SECONDS] [--bytes-per-row N]

options:
  -h, --help                    show this help message and exit
  --version                     show program's version number and exit
  --group GROUP                 Multicast group address to join (default: 239.255.0.0)
  --port PORT                   UDP port to listen on (default: 2040)
  --highlight-duration SECONDS  How long (in seconds) to keep a changed byte
                                highlighted (default: 3.0)
  --bytes-per-row N             Number of bytes displayed per row (default: 16)
```

### Example

```bash
# Listen on a custom group/port, highlight changes for 5 seconds
memory-mapper --group 239.1.2.3 --port 2040 --highlight-duration 5
```

---

## Message format

Send raw UDP multicast datagrams whose payload is the full memory snapshot as a contiguous byte array.  
Each packet **replaces** the previous snapshot; changed offsets are highlighted automatically.

```
┌──────────────────────────────── UDP datagram ────────────────────────────────┐
│  Multicast group  239.255.0.0 (configurable)                                 │
│  Port             2040        (configurable)                                 │
│  Payload          <raw bytes>  e.g. 256 or 4096 bytes of memory              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Display

```
╭──────────────────────────── Memory Mapper ────────────────────────────────╮
│  Offset  00  01  02  03  04  05  06  07  …  │  ASCII                     │
│  0x0000  00  01  02  03  04 [FF] 06  07  …  │  .....ÿ..                  │
│  0x0010 [AB] 11  12  13  14  15  16  17  …  │  «.......                  │
│  …                                                                        │
╰── Packets received: 4  Size: 64 B  Last update: 2026-03-11 15:00:00 ─────╯
```

Bytes shown with `[ ]` above would appear with a **yellow background** in a
real terminal, indicating they changed within the last `--highlight-duration`
seconds.

---

## Pre-built binaries

Each push to `main` and every version tag (`v*`) triggers the [Build & Release](.github/workflows/build.yml) GitHub Actions workflow that produces standalone executables for:

| Platform | Artifact |
|----------|----------|
| Linux (Ubuntu) | `warped-pinball-dash-memory-mapper-linux-amd64-<version>.deb` |
| macOS    | `warped-pinball-dash-memory-mapper-macos-<version>` |
| Windows  | `warped-pinball-dash-memory-mapper-windows-<version>.exe` |

On Ubuntu, download the `.deb` package and open it to install (or run `sudo apt install ./warped-pinball-dash-memory-mapper-linux-amd64-<version>.deb`). This installs a `memory-mapper` command in `/usr/local/bin` without requiring Python to be preinstalled.

Download the appropriate artifact from the **Actions** tab or from the **Releases** page.

---

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest -v
```
