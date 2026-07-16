# memory-mapper

A cross-platform CLI tool that discovers Warped Pinball **Vector** boards on
your network, enables their memory broadcast, and displays the live memory
snapshots in the terminal, **highlighting recently-changed bytes** so you can
hunt down the addresses that drive scores, balls, game state, and anything
else you want to inspect. Once you've found an address, you can also write
values back to the machine's memory straight from the viewer.

Machine discovery, authentication, and memory writes are handled by the
[warpedpinball](https://github.com/warped-pinball/python-library) Python
library.

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

The tool discovers Vector machines on your local network and lets you pick
one (or auto-selects when there's only one). Enter the machine's password
when prompted (or set `$VECTOR_PASSWORD`, or pass `--password`) and the tool
enables the memory broadcast on the machine for you, then starts showing
memory live. Press **Ctrl-C** or **Q** to exit; the broadcast is turned back
off on the way out.

To skip discovery, name the machine directly:

```bash
memory-mapper --machine elvira            # by LAN name (partial names work)
memory-mapper --machine 192.168.1.50      # or by IP
```

If you'd rather enable the broadcast yourself in the Vector web UI (the old
workflow), run with `--listen-only`; the tool then just listens on multicast
group `239.255.0.0` port `2040` without touching the machine. See the
[User Guide](USER_GUIDE.md) for the full walkthrough and troubleshooting.

## Usage

```
usage: memory-mapper [-h] [--version] [--machine NAME_OR_IP]
                     [--password PASSWORD] [--frequency-ms MS]
                     [--discover-timeout SECONDS] [--listen-only]
                     [--keep-broadcasting] [--group GROUP] [--port PORT]
                     [--highlight-duration SECONDS] [--bytes-per-row N]
                     [--source-filter IP]

options:
  -h, --help                    show this help message and exit
  --version                     show program's version number and exit
  --machine NAME_OR_IP          Vector machine to connect to, by LAN name or IP
                                (default: discover and pick interactively)
  --password PASSWORD           Vector password for enabling the broadcast and
                                writing memory (falls back to $VECTOR_PASSWORD,
                                then an interactive prompt)
  --frequency-ms MS             How often the machine broadcasts snapshots
                                (default: 100, clamped to 10-60000)
  --discover-timeout SECONDS    How long to wait for discovery answers (default: 20)
  --listen-only                 Don't discover or control a machine; just listen
                                (enable the broadcast in the Vector web UI yourself)
  --keep-broadcasting           Leave the broadcast enabled on the machine on exit
  --group GROUP                 Multicast group address to join (default: 239.255.0.0)
  --port PORT                   UDP port to listen on (default: 2040)
  --highlight-duration SECONDS  How long changed bytes stay highlighted (default: 3.0)
  --bytes-per-row N             Minimum bytes per row; auto-expands to fill the
                                terminal width (default: 16)
  --source-filter IP            Only process packets from this sender IP
                                (default: the connected machine)
```

Once running, single-key commands let you navigate the memory map, inspect
individual bytes, and iteratively filter offsets by how each byte changed
(changed, unchanged, increased, decreased, and bit-level variants) to track down
the values you care about. Press **W** to write value(s) to memory at the
cursor — every write shows the details and a warning first, and nothing is
sent until you confirm. The [User Guide](USER_GUIDE.md) documents every
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
