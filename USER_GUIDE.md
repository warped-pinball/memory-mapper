# Memory Mapper — User Guide

Memory Mapper is a live, terminal-based viewer for memory snapshots broadcast
from a Warped Pinball **Vector** board. It shows the raw bytes of the machine's
memory as they change in real time, so you can hunt for the addresses that
drive scores, balls, game state, and anything else you want to inspect.

---

## Before you start: enable broadcasting on Vector

Memory Mapper is **only a listener** — it doesn't poke Vector or pull data from
it. Vector has to be actively broadcasting its memory over the local network
for anything to show up in the tool.

1. Open the Vector web UI.
2. Enable the **"Broadcast Memory Snapshots on Vector"** toggle.
3. Make sure the computer running `memory-mapper` is on the **same local
   network** as Vector. Broadcast traffic is UDP multicast
   (`239.255.0.0:2040` by default) and will not cross routers, VPNs, or
   guest-Wi-Fi isolation.

If you don't see any data after starting `memory-mapper`, check those two
things first:

- Is the broadcast toggle actually on?
- Are you on the same subnet as Vector (not a guest SSID, not a different VLAN,
  not tethered to your phone)?

---

## Installing

### Pre-built binaries

Download the binary for your platform from the
[Releases](https://github.com/warped-pinball/memory-mapper/releases) page:

| Platform          | Artifact                                                   |
|-------------------|------------------------------------------------------------|
| Ubuntu x86_64     | `warped-pinball-memory-mapper-linux-amd64-<version>.deb`   |
| Raspberry Pi      | `warped-pinball-memory-mapper-linux-arm64-raspberry-pi-<version>.deb` |
| macOS             | `warped-pinball-memory-mapper-macos-<version>`             |
| Windows           | `warped-pinball-memory-mapper-windows-<version>.exe`       |

On Ubuntu, install the `.deb` with `sudo apt install ./<file>.deb`. On macOS
and Windows, run the downloaded executable directly.

### From source

```bash
pip install .
memory-mapper
```

---

## Running it

```bash
memory-mapper
```

That's it for the common case. The tool will join the multicast group, wait
for the first packet from Vector, auto-select that sender, and start showing
memory live.

### Command-line options

```
usage: memory-mapper [-h] [--version] [--group GROUP] [--port PORT]
                     [--highlight-duration SECONDS] [--bytes-per-row N]
                     [--source-filter IP]

  --group GROUP                 Multicast group to join (default: 239.255.0.0)
  --port PORT                   UDP port to listen on (default: 2040)
  --highlight-duration SECONDS  How long changed bytes stay highlighted
                                (default: 3.0)
  --bytes-per-row N             Minimum bytes per row; auto-expands to fill
                                a wider terminal (default: 16)
  --source-filter IP            Only process packets from this sender IP
```

---

## Reading the screen

```
┌──────────────── Memory Mapper ────────────────┐
│  Off  00 01 02 03 04 05 06 07 08 09 0A ...    │
│ 0000  00 01 02 03 04 FF 06 07 08 09 0A ...    │
│ 0010  AB 11 12 13 14 15 16 17 18 19 1A ...    │
│  ...                                          │
└── Packets 123  Size 256B  Highlight 3.0s  ────┘
┌──────────────── Cursor ───────────────────────┐
│  Addr: 0x0005 (5)  Hex: 0xFF  Dec: 255        │
│  Bin: 11111111  ASCII: ·                      │
│  History (last 10): 0x00(3.2s ago) ...        │
└───────────────────────────────────────────────┘
┌──────────────── Legend ───────────────────────┐
│  Hard match  Soft(1 miss)  Soft(2 misses)     │
│  Recently changed  Cursor  Marked             │
└───────────────────────────────────────────────┘
┌──────────────── Menu ─────────────────────────┐
│  Sources: 1:192.168.1.42*                     │
│  Scan: C changed  N unchanged  ...            │
│  Nav: ←↑↓→ Space mark  E export  ...          │
│  Status: Ready                                │
└───────────────────────────────────────────────┘
```

- **Memory panel** — a classic hex dump. A yellow background means the byte
  changed within the last few seconds.
- **Cursor panel** — details for the byte under the cursor: address, hex,
  decimal, binary, ASCII, and recent value history.
- **Legend** — the meaning of every highlight color.
- **Menu** — sources, scan commands, navigation, and the current status line.

---

## Keyboard controls

### Navigation

| Key       | Action                                         |
|-----------|------------------------------------------------|
| `← ↑ ↓ →` | Move the cursor                                |
| `Space`   | Mark (or unmark) the byte under the cursor     |
| `1`–`9`   | Switch to source 1–9 (resets tracker state)    |
| `T`       | Toggle ASCII view on the hex dump              |
| `B`       | Toggle between BYTE mode and BIT mode          |
| `+` / `-` | Increase / decrease the change-highlight time  |
| `Q`       | Quit                                           |

### Exporting

| Key | Action                                                          |
|-----|-----------------------------------------------------------------|
| `E` | Export all marked addresses to `marked_addresses_<ts>.json`     |
| `X` | Export the full snapshot to `all_addresses_<ts>.json`           |

Files are written to the current working directory.

---

## Hunting for addresses: the scan workflow

The scan is the killer feature. It's modeled on the "Cheat Engine" style of
iterative filtering: you tell Memory Mapper *what you just saw happen*, and it
narrows down the set of addresses that could possibly be the value you're
looking for.

### Byte mode scans

Press `B` until the menu shows `mode:BYTE`.

| Key | Meaning                                  |
|-----|------------------------------------------|
| `C` | Bytes that **changed** since last scan   |
| `N` | Bytes that did **not** change            |
| `I` | Bytes whose value **increased**          |
| `D` | Bytes whose value **decreased**          |
| `A` | Any / not sure — just recapture baseline |
| `R` | Reset the scan entirely                  |

### Bit mode scans

Press `B` until the menu shows `mode:BIT`. The layout is identical to byte
mode — the memory is still shown as a hex dump — but the scan options become
bit-level:

| Key | Meaning                                      |
|-----|----------------------------------------------|
| `C` | Any bit **changed** since last scan          |
| `N` | Bits did **not** change                      |
| `S` | Bits that are currently **set** (`=1`)       |
| `L` | Bits that are currently **cleared** (`=0`)   |
| `A` | Any / not sure — just recapture baseline     |
| `R` | Reset the bit scan                           |

In bit mode, a byte is highlighted based on the best match level of any of its
8 bits, and the cursor panel colors the individual bits of the `Bin:` value so
you can see exactly which bits are matching.

Bit and byte scans are tracked independently — switching modes does not reset
the other.

### Example: finding the credit counter

1. Start Memory Mapper with the machine idle.
2. Press `R` to reset the scan and capture a fresh baseline.
3. Drop a credit on the pinball machine.
4. Press `I` (increased) — the view now only highlights bytes that went up.
5. Play a game until you use a credit, then press `D` (decreased).
6. Repeat until only a handful of green "hard match" bytes remain.
7. Move the cursor over each candidate, press `Space` to mark it, and then
   press `E` to save your finds to a JSON file.

### Match colors

- **Green** — hard match: this byte matched **every** scan step you ran.
- **Cyan** — soft match: missed **one** step (useful for noisy memory).
- **Magenta** — soft match: missed **two** steps.
- **Yellow** — the byte changed within the last `highlight-duration` seconds.
- **White** — the cursor.
- **Red** — a marked address.

---

## Switching sources

If multiple Vector boards are broadcasting on your network, the menu's
`Sources:` line lists them numbered `1` through `9`. Press the matching number
key to switch. Switching sources resets the tracker so you're not mixing state
from two different boards.

---

## Troubleshooting

**Nothing appears, it just says "Waiting for data…".**
Check that **"Broadcast Memory Snapshots on Vector"** is enabled in the
Vector web UI, and that your computer is on the same local network as Vector.
Multicast traffic does not cross routers, VPNs, or Wi-Fi client isolation.

**The `Sources:` list is empty.**
No packets have been received yet. Same causes as above — the tool hasn't
seen anything on the wire.

**The Rate / Refresh values are zero.**
Vector has stopped broadcasting, or the network dropped the multicast stream.
Toggle the broadcast setting off and on again in the Vector UI.

**The view is cramped / wraps awkwardly.**
Resize your terminal wider. Memory Mapper auto-expands the bytes-per-row to
fit the available width.

**I'm on a different subnet than Vector.**
Move onto the same LAN. Memory Mapper uses UDP multicast, which is not
designed to be routed.
