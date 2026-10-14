# lw-px900

Upload text labels from a Mac to the saved-label memory of an Epson LW-PX900 label printer over USB.
Once a label is saved, you can print it from the printer's keypad without a computer.

Epson's own transfer tool (Data Transfer Tool, `DataTT.exe`) runs only on Windows.
This script speaks the same USB protocol and writes the same label format as Epson's Windows Label Editor.

## Requirements

- macOS with Homebrew (Linux should also work with libusb, but is untested)
- Python 3.9 or newer
- An LW-PX900 connected by USB and switched on

## Setup

```sh
brew install libusb
git clone https://github.com/aki263/lw-px900.git
cd lw-px900
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Check that the printer is visible:

```sh
.venv/bin/python px900.py probe 1
```

A reply starting with `slot 1:` means the connection works.
`error: LW-PX900 is not connected over USB` means the printer is off, unplugged, or held by another app such as Epson Label Editor.
Quit other label apps before running the script.

## Save one label

```sh
.venv/bin/python px900.py send 5 "CORE SW1 P49 -> EDGE SW3 P1"
```

This saves the text into memory slot 5.
Give each line as its own argument for a multi-line label:

```sh
.venv/bin/python px900.py send 6 "SERVER A" "eth0"
```

On the printer, open the saved-label list and pick the slot to print it.

## Save many labels from a CSV file

Each row becomes one label, and each non-empty column becomes one line of that label.
The first row is treated as a header and skipped.

```sh
.venv/bin/python px900.py send-csv examples/labels.csv --start-slot 10 --dry-run
.venv/bin/python px900.py send-csv examples/labels.csv --start-slot 10
```

`--dry-run` prints which label goes to which slot without touching the printer.
Rows are written to consecutive slots, starting at `--start-slot`.

| Option | Effect |
| --- | --- |
| `--unique` | Skip rows that repeat an earlier row |
| `--no-header` | Treat the first row as a label |
| `--overwrite` | Replace labels already saved in those slots |

The command stops at the first slot that fails, so earlier slots keep their new labels.

## Slots and limits

- The printer has slots 1 through 100.
- Every write command refuses a slot that is already used, unless you pass `--overwrite`.
  To find a free slot, run `probe`: a free slot answers `52 56 3b 01 00 00 00 00 00 3b 1f`.
- Up to 500 lines per label are accepted by the script, matching Epson Label Editor's limit.
  How many lines actually fit when printing depends on the tape width and the printer's own layout rules; long labels have not been tested.
- Lines must not be empty or contain control characters such as tabs or line breaks.

## Other commands

| Command | What it does |
| --- | --- |
| `probe SLOT` | Ask the printer whether a slot is used |
| `build FILE LINE...` | Write a label to a `.dt3` file instead of sending it |
| `upload SLOT FILE` | Send a `.dt3` file made by `build` |
| `inspect FILE` | Check that a `.dt3` file has a valid header |

## How it works

See [FORMAT.md](FORMAT.md) for the USB commands and the label byte layout.
Labels are always written as plain horizontal text with the printer's default font and layout.
Change fonts, sizes, or frames on the printer after the label is saved.

## Disclaimer

This project is not made or supported by Epson.
It was tested on one LW-PX900.
Use it at your own risk.
