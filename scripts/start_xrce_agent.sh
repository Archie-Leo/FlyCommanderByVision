#!/usr/bin/env bash
set -euo pipefail
DEVICE="${1:-/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0}"
BAUD="${2:-57600}"
[[ -e "$DEVICE" && -c "$(readlink -f "$DEVICE")" ]] || {
  echo "Serial device unavailable: $DEVICE" >&2; exit 2;
}
[[ "$BAUD" =~ ^[0-9]+$ ]] || { echo 'Baud rate must be numeric' >&2; exit 2; }
if [[ -r "$DEVICE" && -w "$DEVICE" ]]; then
  exec MicroXRCEAgent serial -D "$DEVICE" -b "$BAUD"
fi
exec sudo -n MicroXRCEAgent serial -D "$DEVICE" -b "$BAUD"
