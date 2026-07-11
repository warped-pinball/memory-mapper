# memory-mapper

A cross-platform CLI tool that listens for UDP multicast memory snapshots from a
Warped Pinball **Vector** board and displays them live in the terminal,
**highlighting recently-changed bytes** so you can hunt down the addresses that
drive scores, balls, game state, and anything else you want to inspect.

## Screenshots

_Screenshots coming soon._

<!--
Planned screenshots (add to docs/images/ and uncomment):
1. Live memory view with recently-changed bytes highlighted in yellow.
2. Cursor inspector panel (Addr / Hex / Dec / Bin / ASCII + value history).
3. Byte-mode scan showing green "hard match" and cyan/magenta "soft match" colors with the legend.
4. Bit-mode scan with the per-bit colored Bin: value in the cursor panel.
5. Marked addresses (the red ">" markers) just before an export.
6. Multiple sources listed on the menu's Sources: line.

![Live memory view](docs/images/memory-view.png)
![Cursor inspector](docs/images/cursor-panel.png)
![Scan in progress](docs/images/scan.png)
-->

## Install

### Pre-built binaries (recommended)

Download the latest build for your platform — these links always point to the
newest release:

| Platform | Download |
|----------|----------|
| Linux (Ubuntu x86_64) | [`warped-pinball-memory-mapper-linux-amd64.deb`](https://github.com/warped-pinball/memory-mapper/releases/latest/download/warped-pinball-memory-mapper-linux-amd64.deb) |
| Raspberry Pi (Ubuntu ARM64) | [`warped-pinball-memory-mapper-linux-arm64-raspberry-pi.deb`](https://github.com/warped-pinball/memory-mapper/releases/latest/download/warped-pinball-memory-mapper-linux-arm64-raspberry-pi.deb) |
| macOS | [`warped-pinball-memory-mapper-macos`](https://github.com/warped-pinball/memory-mapper/releases/latest/download/warped-pinball-memory-mapper-macos) |
| Windows | [`warped-pinball-memory-mapper-windows.exe`](https://github.com/warped-pinball/memory-mapper/releases/latest/download/warped-pinball-memory-mapper-windows.exe) |

On Ubuntu or Raspberry Pi, install the `.deb` with
`sudo apt install ./<file>.deb`; this places a `memory-mapper` command in
`/usr/local/bin` without requiring Python. On macOS and Windows, run the
downloaded executable directly.

### From source

```bash
pip install .
memory-mapper
```

## Quick start

```bash
memory-mapper
```

By default the tool joins multicast group `239.255.0.0` on port `2040`, waits
for the first packet from Vector, and starts showing memory live. Press
**Ctrl-C** to exit.

Vector has to be actively broadcasting for anything to appear — enable the
**"Broadcast Memory Snapshots on Vector"** toggle in the Vector web UI, and make
sure your computer is on the same local network. See the
[User Guide](USER_GUIDE.md) for the full walkthrough and troubleshooting.

## Usage

```
usage: memory-mapper [-h] [--version] [--group GROUP] [--port PORT]
                     [--highlight-duration SECONDS] [--bytes-per-row N]
                     [--source-filter IP]

options:
  -h, --help                    show this help message and exit
  --version                     show program's version number and exit
  --group GROUP                 Multicast group address to join (default: 239.255.0.0)
  --port PORT                   UDP port to listen on (default: 2040)
  --highlight-duration SECONDS  How long changed bytes stay highlighted (default: 3.0)
  --bytes-per-row N             Minimum bytes per row; auto-expands to fill the
                                terminal width (default: 16)
  --source-filter IP            Only process packets from this sender IP
```

Once running, single-key commands let you navigate the memory map, inspect
individual bytes, and iteratively filter offsets by how each byte changed
(changed, unchanged, increased, decreased, and bit-level variants) to track down
the values you care about. The [User Guide](USER_GUIDE.md) documents every
keyboard control and walks through the scan workflow.

## Documentation

- **[User Guide](USER_GUIDE.md)** — installing, running, reading the screen,
  keyboard controls, the scan workflow, and troubleshooting.
- **[Contributing](CONTRIBUTING.md)** — development setup, the message format,
  and how builds and releases work.

## License

Memory Mapper is released under the [PolyForm Shield License 1.0.0](LICENSE).
You may use it for any purpose, including commercially — except to provide a
product that competes with Warped Pinball's products or services.
