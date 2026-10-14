# Protocol and file format

These notes come from reading Epson's Windows Label Editor (`LabelEditor.exe`), Data Transfer Tool (`DataTT.exe`), and the printer driver.
They were checked against a real LW-PX900.

## USB

| Item | Value |
| --- | --- |
| Vendor / product ID | `04b8` / `0705` |
| Bulk OUT / IN endpoints | `0x01` / `0x82` |
| Session start | `1b 7b 04 4a 01 4b 7d` |
| Session end | `1b 7b 04 4a 00 4a 7d` |

Before each session the client reads vendor control request 2, which returns `02 00 00` when the printer is ready.

## Commands

A command is `1b 7b 09 4d`, six body bytes, a checksum, and `7d`.
The body is `01 KIND SLOT 00 LEN_HI LEN_LO`.
The checksum is the low byte of `0x4d` plus the body bytes.

| Kind | Meaning |
| --- | --- |
| `00` | Query a slot |
| `01` | Read a slot |
| `02` | Write a slot; the command is followed by `DT;` + payload + `;` + checksum |

The write checksum is the low byte of the sum of `DT;`, the payload, and `;`.
Replies start with `RV;` and end with a checksum byte.
A successful write replies `RV; 01 02 00 ...`.
A rejected payload replies `RV; 01 02 FE ...`.

## Label payload

The payload is the format Label Editor writes for a horizontal text label.
The printer converts it to a different internal format when it saves the label, so data read back from a slot cannot be written back unchanged.

```
01 01                     label kind
00 00 00 00
02 00 <20 bytes>          label attributes
00 00
08 00 <36 bytes>          default line attributes
00 00
00 00 <26 bytes>          layout attributes
00 00
30 00 <20 bytes> 00 00    one block
  10 00 <36 bytes> 00 00  one line
    80 00 HH LL ...       each character as 80 00 + UTF-16BE code unit
  00 00
75 00 <10 bytes>          trailer attributes
00 00
0d 0a
```

The attribute blocks are copied from constants in `LabelEditor.exe`; `px900.py` holds them as `BLOCK_ATTRS`, `LINE_ATTRS`, `LAYOUT_ATTRS`, and `TRAILER_ATTRS`.
Payload length is 116 bytes, plus 24 per block, plus 40 per line, plus 4 per character and 2 per non-empty line.

A `.dt3` file is the payload prefixed by `Cable Labeling Data` in UTF-16LE, zero-padded to 64 bytes, and then `01 00`.
