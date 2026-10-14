#!/usr/bin/env python3
import argparse
import csv
import sys
from pathlib import Path

import usb.core
import usb.util


VENDOR_ID = 0x04B8
PRODUCT_ID = 0x0705
OUT_ENDPOINT = 0x01
IN_ENDPOINT = 0x82
START = bytes.fromhex("1b 7b 04 4a 01 4b 7d")
END = bytes.fromhex("1b 7b 04 4a 00 4a 7d")
FILE_HEADER = "Cable Labeling Data".encode("utf-16le").ljust(64, b"\0") + b"\x01\x00"
EMPTY_SLOT_RESPONSE = bytes.fromhex("52 56 3b 01 00 00 00 00 00 3b 1f")

# Byte layout reproduces LabelEditor.exe FUN_004dc700 for a horizontal text label (type 2).
SEPARATOR = b"\0\0"
BLOCK_ATTRS = bytes.fromhex("00 01 02 04 01 0f 08 01 01 0c 01 00 10 01 00 11 02 00 00 00")
LINE_ATTRS = bytes.fromhex(
    "00 01 00 04 01 00 08 01 01 09 01 00 0c 01 00 0d 01 00"
    "10 01 00 11 01 00 14 01 01 15 01 00 18 01 00 19 01 00"
)
# Value 02 at offset 2 matches the labels the printer saves from its own keypad.
LAYOUT_ATTRS = bytes.fromhex("00 01 02 04 02 00 00 0d 01 00 05 01 00 08 01 00 09 01 00 0a 01 00 0c 01 00 00")
TRAILER_ATTRS = bytes.fromhex("00 08 02 00 00 00 00 00 00 00")
MAX_LINES = 500


def encode_line(line: str) -> bytes:
    units = line.encode("utf-16-be")
    chars = b"".join(b"\x80\x00" + units[i : i + 2] for i in range(0, len(units), 2))
    return b"\x10\x00" + LINE_ATTRS + SEPARATOR + chars + SEPARATOR


def encode_text_label(lines: list[str]) -> bytes:
    if not lines or not all(lines):
        raise ValueError("label needs at least one line and no empty lines")
    if any(not line.isprintable() for line in lines):
        raise ValueError("label lines must not contain control characters")
    if len(lines) > MAX_LINES:
        raise ValueError(f"label exceeds {MAX_LINES} lines")
    content = b"\x30\x00" + BLOCK_ATTRS + SEPARATOR + b"".join(encode_line(line) for line in lines)
    return (
        b"\x01\x01" + SEPARATOR + SEPARATOR
        + b"\x02\x00" + BLOCK_ATTRS + SEPARATOR
        + b"\x08\x00" + LINE_ATTRS + SEPARATOR
        + SEPARATOR + LAYOUT_ATTRS + SEPARATOR
        + content
        + b"\x75\x00" + TRAILER_ATTRS + SEPARATOR
        + b"\r\n"
    )


def load_dt3(path: Path) -> bytes:
    data = path.read_bytes()
    if not data.startswith(FILE_HEADER):
        raise ValueError(f"{path}: unsupported .dt3 header")
    payload = bytearray(data[len(FILE_HEADER) :])
    if not payload or len(payload) > 0xFFFF:
        raise ValueError(f"{path}: payload must be 1 to 65535 bytes")
    payload[0] = 1
    return bytes(payload)


def command(kind: int, slot: int, length: int = 0) -> bytes:
    if not 1 <= slot <= 100:
        raise ValueError("slot must be from 1 to 100")
    if not 0 <= length <= 0xFFFF:
        raise ValueError("payload exceeds protocol limit")
    body = bytes((1, kind, slot, 0, length >> 8, length & 0xFF))
    checksum = (ord("M") + sum(body)) & 0xFF
    return bytes.fromhex("1b 7b 09 4d") + body + bytes((checksum, 0x7D))


def transfer_packet(slot: int, payload: bytes) -> bytes:
    marker = b"DT;" + payload + b";"
    checksum = sum(marker) & 0xFF
    return command(2, slot, len(payload)) + marker + bytes((checksum,))


def open_printer():
    device = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if device is None:
        raise RuntimeError("LW-PX900 is not connected over USB")
    usb.util.claim_interface(device, 0)
    try:
        device.ctrl_transfer(0xC1, 1, 0, 0, 20, timeout=2000)
        status = bytes(device.ctrl_transfer(0xC1, 2, 0, 0, 3, timeout=2000))
        if status != b"\x02\x00\x00":
            raise RuntimeError(f"printer is not ready: {status.hex(' ')}")
    except Exception:
        usb.util.release_interface(device, 0)
        usb.util.dispose_resources(device)
        raise
    return device


def read_response(device) -> bytes:
    response = bytearray()
    while True:
        try:
            response.extend(device.read(IN_ENDPOINT, 4096, timeout=1200))
        except usb.core.USBTimeoutError:
            break
    return bytes(response)


def check_response(response: bytes) -> bytes:
    if len(response) < 11 or not response.startswith(b"RV;"):
        raise RuntimeError(f"unexpected printer response: {response.hex(' ')}")
    if sum(response[:-1]) & 0xFF != response[-1]:
        raise RuntimeError(f"invalid printer response checksum: {response.hex(' ')}")
    return response


def exchange(packet: bytes) -> bytes:
    device = open_printer()
    try:
        device.write(OUT_ENDPOINT, START, timeout=5000)
        device.write(OUT_ENDPOINT, packet, timeout=10000)
        return check_response(read_response(device))
    finally:
        try:
            device.write(OUT_ENDPOINT, END, timeout=5000)
        finally:
            usb.util.release_interface(device, 0)
            usb.util.dispose_resources(device)


def upload_payload(slot: int, payload: bytes, overwrite: bool) -> None:
    before = exchange(command(0, slot))
    if before != EMPTY_SLOT_RESPONSE and not overwrite:
        raise RuntimeError(
            f"slot {slot} does not match a confirmed empty slot; "
            "use --overwrite only if its contents can be replaced"
        )
    response = exchange(transfer_packet(slot, payload))
    if response[3:6] != bytes((1, 2, 0)):
        raise RuntimeError(f"printer rejected transfer to slot {slot}: {response.hex(' ')}")
    after = exchange(command(0, slot))
    if after[5] != slot:
        raise RuntimeError(f"printer did not retain slot {slot}: {after.hex(' ')}")
    print(f"slot {slot}: stored {len(payload)} bytes")


def read_csv_labels(path: Path, header: bool, unique: bool) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))
    if header:
        rows = rows[1:]
    labels = [[cell.strip() for cell in row if cell.strip()] for row in rows]
    labels = [label for label in labels if label]
    if unique:
        labels = [list(key) for key in dict.fromkeys(tuple(label) for label in labels)]
    return labels


def run_probe(args) -> None:
    print(f"slot {args.slot}: {exchange(command(0, args.slot)).hex(' ')}")


def run_inspect(args) -> None:
    print(f"{args.file.name}: {len(load_dt3(args.file))} transfer bytes")


def run_build(args) -> None:
    payload = encode_text_label(args.lines)
    args.file.write_bytes(FILE_HEADER + payload)
    print(f"{args.file.name}: {len(payload)} transfer bytes")


def run_upload(args) -> None:
    upload_payload(args.slot, load_dt3(args.file), args.overwrite)


def run_send(args) -> None:
    upload_payload(args.slot, encode_text_label(args.lines), args.overwrite)


def run_send_csv(args) -> None:
    labels = read_csv_labels(args.file, not args.no_header, args.unique)
    if args.start_slot + len(labels) - 1 > 100:
        raise ValueError(f"{len(labels)} labels starting at slot {args.start_slot} exceed slot 100")
    payloads = [encode_text_label(label) for label in labels]
    for slot, (label, payload) in enumerate(zip(labels, payloads), start=args.start_slot):
        print(f"slot {slot}: {' / '.join(label)}")
        if not args.dry_run:
            upload_payload(slot, payload, args.overwrite)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Epson LW-PX900 USB label transfer")
    subparsers = parser.add_subparsers(dest="action", required=True)

    probe = subparsers.add_parser("probe", help="query one printer memory slot")
    probe.add_argument("slot", type=int)
    probe.set_defaults(run=run_probe)

    inspect = subparsers.add_parser("inspect", help="check a .dt3 file without USB access")
    inspect.add_argument("file", type=Path)
    inspect.set_defaults(run=run_inspect)

    build = subparsers.add_parser("build", help="write a text label as a .dt3 file")
    build.add_argument("file", type=Path)
    build.add_argument("lines", nargs="+", help="one argument per printed line")
    build.set_defaults(run=run_build)

    upload = subparsers.add_parser("upload", help="send one .dt3 file to a printer memory slot")
    upload.add_argument("slot", type=int)
    upload.add_argument("file", type=Path)
    upload.set_defaults(run=run_upload)

    send = subparsers.add_parser("send", help="send text straight to a printer memory slot")
    send.add_argument("slot", type=int)
    send.add_argument("lines", nargs="+", help="one argument per printed line")
    send.set_defaults(run=run_send)

    send_csv = subparsers.add_parser("send-csv", help="send each CSV row to consecutive slots")
    send_csv.add_argument("file", type=Path)
    send_csv.add_argument("--start-slot", type=int, required=True)
    send_csv.add_argument("--no-header", action="store_true", help="the first row is a label")
    send_csv.add_argument("--unique", action="store_true", help="skip repeated rows")
    send_csv.add_argument("--dry-run", action="store_true", help="show the slot plan only")
    send_csv.set_defaults(run=run_send_csv)

    for writer in (upload, send, send_csv):
        writer.add_argument("--overwrite", action="store_true", help="allow replacing a used slot")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.run(args)
        return 0
    except (OSError, ValueError, RuntimeError, usb.core.USBError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
