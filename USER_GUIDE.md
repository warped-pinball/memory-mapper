# Memory Mapper — User Guide

Memory Mapper is a live, terminal-based viewer for memory snapshots broadcast
from a Warped Pinball **Vector** board. It shows the raw bytes of the machine's
memory as they change in real time, so you can hunt for the addresses that
drive scores, balls, game state, and anything else you want to inspect.

---

## Before you start

Memory Mapper continuously looks for Vector boards on your network in the
background and turns on the memory broadcast for you — no need to touch the
Vector web UI. You just need two things:

1. The computer running `memory-mapper` must be on the **same local network**
   as Vector. Discovery and the memory stream are UDP broadcast traffic
   (`239.255.0.0:2040` by default) and will not cross routers, VPNs, or
   guest-Wi-Fi isolation.
2. The **Vector password**, because enabling the broadcast (and writing to
   memory) are authenticated operations. The app prompts for it the first
   time it's needed; you can also pass `--password`, set the
   `$VECTOR_PASSWORD` environment variable, or press `P` in the app at any
   time.

If you'd rather flip the **"Broadcast Memory Snapshots on Vector"** toggle in
the Vector web UI yourself, run with `--listen-only`; the tool then behaves as
a pure listener and never touches the machine (memory writes are unavailable
in this mode).

---

## Installing

### Pre-built binaries

Download the latest build for your platform — these links always point to the
newest [release](https://github.com/warped-pinball/memory-mapper/releases):

| Platform          | Download                                                   |
|-------------------|------------------------------------------------------------|
| Ubuntu x86_64     | [`...linux-amd64.deb`](https://github.com/warped-pinball/memory-mapper/releases/latest/download/warped-pinball-memory-mapper-linux-amd64.deb) |
| Raspberry Pi      | [`...linux-arm64-raspberry-pi.deb`](https://github.com/warped-pinball/memory-mapper/releases/latest/download/warped-pinball-memory-mapper-linux-arm64-raspberry-pi.deb) |
| macOS             | [`...macos`](https://github.com/warped-pinball/memory-mapper/releases/latest/download/warped-pinball-memory-mapper-macos) |
| Windows           | [`...windows.exe`](https://github.com/warped-pinball/memory-mapper/releases/latest/download/warped-pinball-memory-mapper-windows.exe) |

On Ubuntu or Raspberry Pi, install the `.deb` with `sudo apt install ./<file>.deb`.
On macOS and Windows, run the downloaded executable directly.

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

That's it for the common case. The viewer opens immediately and, in the
background:

1. Continuously discovers Vector machines on your network (discovered
   machines show up in the "Waiting for data…" panel and in the `Sources:`
   list, by name).
2. When a machine is found, asks for its password in the app (skipped if
   `--password` or `$VECTOR_PASSWORD` is set — press `Esc` to stay a passive
   listener, and `P` later if you change your mind).
3. Enables the memory broadcast on the machine and starts showing memory
   live.

When you quit (`Q` or Ctrl-C), any broadcast the tool enabled is turned back
off unless you pass `--keep-broadcasting`.

If several machines are on the network, focus on one:

```bash
memory-mapper --machine elvira          # LAN name; partial names work
memory-mapper --machine 192.168.1.50    # or straight to an IP
```

### Command-line options

```
usage: memory-mapper [-h] [--version] [--machine NAME_OR_IP]
                     [--password PASSWORD] [--frequency-ms MS]
                     [--discover-timeout SECONDS] [--listen-only]
                     [--keep-broadcasting] [--group GROUP] [--port PORT]
                     [--highlight-duration SECONDS] [--bytes-per-row N]
                     [--source-filter IP]

  --machine NAME_OR_IP          Vector machine to focus on, by LAN name or
                                IP (default: the first machine discovered)
  --password PASSWORD           Vector password (falls back to
                                $VECTOR_PASSWORD; otherwise the app prompts
                                when it's needed)
  --frequency-ms MS             How often the machine broadcasts snapshots
                                (default: 100, clamped to 10-60000)
  --discover-timeout SECONDS    How long each background discovery round
                                listens (default: 5)
  --listen-only                 Pure listener mode; never discover or control
                                machines
  --keep-broadcasting           Leave the broadcast enabled on exit
  --group GROUP                 Multicast group to join (default: 239.255.0.0)
  --port PORT                   UDP port to listen on (default: 2040)
  --highlight-duration SECONDS  How long changed bytes stay highlighted
                                (default: 3.0)
  --bytes-per-row N             Minimum bytes per row; auto-expands to fill
                                a wider terminal (default: 16)
  --source-filter IP            Only process packets from this sender IP
                                (default: the connected machine)
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
| `W`       | Write value(s) to memory at the cursor         |
| `P`       | Enter (or change) the Vector password          |
| `1`–`9`   | Switch to source 1–9 (resets tracker state)    |
| `T`       | Toggle ASCII view on the hex dump              |
| `B`       | Toggle between BYTE mode and BIT mode          |
| `+` / `-` | Increase / decrease the change-highlight time  |
| `Q`       | Quit                                           |

### View toggles

When the memory map is large, screen space gets tight. Each part of the UI can
be hidden independently so the hex dump gets as much room as possible:

| Key | Action                                                              |
|-----|---------------------------------------------------------------------|
| `M` | Toggle the menu panel (replaced by a one-line status bar when hidden) |
| `K` | Toggle the legend panel                                             |
| `O` | Toggle the offset column and column-header row                      |
| `U` | Toggle the cursor info panel                                        |
| `V` | Toggle compact view (removes panel borders and padding)             |

### Exporting

| Key | Action                                                          |
|-----|-----------------------------------------------------------------|
| `E` | Export all marked addresses to `marked_addresses_<ts>.json`     |
| `X` | Export the full snapshot to `all_addresses_<ts>.json`           |

Files are written to the current working directory.

---

## Writing to memory

Once you've found an address, you can change its value directly from the
viewer. Move the cursor onto the byte and press `W`:

1. **Enter the value(s).** Type a byte value in decimal (`5`) or hex
   (`0x2F`). Separate multiple values with spaces to write a run of
   consecutive addresses, e.g. `0x12 0x34 0x56`. Press `Enter` to continue or
   `Esc` to cancel.
2. **Confirm.** A red confirmation panel shows the machine, the address, the
   current value(s), and the new value(s), along with a warning:

   > ⚠ Caution, writing values to memory can have unexpected or harmful
   > effects, do so with caution.

   Press `Y` to perform the write; **any other key cancels**.

The write goes over the network as an authenticated request. If no password
has been entered yet, pressing `W` asks for it first and then continues the
write. In `--listen-only` mode, `W` reports that writes are unavailable.

Writes land in the machine's live, battery-backed game memory. Writing the
wrong offset can corrupt scores or settings, or crash the game in progress —
verify offsets against a known-good memory map for the exact ROM the machine
is running.

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
- **`>`** — a red `>` to the left of the value indicates a marked address.

---

## Switching sources

The menu's `Sources:` line lists every machine on your network, numbered `1`
through `9`, by name — both the ones actively broadcasting and the ones
discovery found that are still silent (shown dimmed). Press the matching
number key to switch.

Selecting a machine that isn't broadcasting yet automatically sends it an
authenticated request to start broadcasting (prompting for the password first
if one hasn't been entered). Switching sources resets the tracker so you're
not mixing state from two different boards.

---

## Troubleshooting

**Nothing appears, it just says "Waiting for data…".**
Make sure your computer is on the same local network as Vector — the memory
stream is UDP broadcast traffic and does not cross routers, VPNs, or Wi-Fi
client isolation. In `--listen-only` mode, also check that **"Broadcast
Memory Snapshots on Vector"** is enabled in the Vector web UI.

**No machines are being discovered.**
Discovery uses UDP broadcast on port 37020 and needs the same-subnet rules as
above. The tool keeps retrying in the background, so a machine that boots up
later will still be found. If discovery can't work on your network, enable
the broadcast in the Vector web UI and the tool will pick up the data anyway.

**Status says "Could not enable memory broadcast…".**
The password was wrong, or the machine was unreachable. Press `P` to re-enter
the password (the tool retries automatically). If the machine's firmware is
too old to support the broadcast toggle route, update it, or enable the
toggle in the Vector web UI instead.

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
