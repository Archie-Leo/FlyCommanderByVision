#!/usr/bin/env bash
set -Eeuo pipefail
sudo systemctl stop fcv-stack.target fcv-runtime.service fcv-telemetry.service fcv-health.service fcv-recorder.service fcv-xrce.service
sudo systemctl disable fcv-stack.target
for unit in fcv-stack.target fcv-xrce.service fcv-telemetry.service fcv-runtime.service fcv-health.service fcv-recorder.service; do
  sudo rm -f "/etc/systemd/system/$unit"
done
sudo rm -f /usr/local/bin/fcvctl
sudo systemctl daemon-reload
echo 'FCV autostart removed. /etc/fcv/fcv.env preserved for recovery.'
