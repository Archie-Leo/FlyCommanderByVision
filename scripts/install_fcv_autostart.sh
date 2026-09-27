#!/usr/bin/env bash
set -Eeuo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ "$repo" == /home/lckfb/FlyCommanderByVision ]] || {
  echo "Install from /home/lckfb/FlyCommanderByVision, got $repo" >&2; exit 1;
}
sudo install -d -m 0755 /etc/fcv
sudo install -d -o lckfb -g lckfb -m 0700 /var/lib/fcv
if [[ ! -s /etc/fcv/ground_command.key ]]; then
  sudo /usr/bin/python3 - <<'PY'
import os
import secrets
fd = os.open('/etc/fcv/ground_command.key', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
try:
    os.write(fd, secrets.token_hex(32).encode())
finally:
    os.close(fd)
PY
  sudo chown root:lckfb /etc/fcv/ground_command.key
fi
if [[ ! -e /etc/fcv/fcv.env ]]; then
  sudo install -m 0644 "$repo/deploy/fcv/fcv.env.example" /etc/fcv/fcv.env
fi
for setting in GROUND_COMMAND_PORT=5604 GROUND_COMMAND_KEY=/etc/fcv/ground_command.key \
               GROUND_COMMAND_ENABLED=0 GROUND_TAKEOFF_HEIGHT_VERIFIED=0; do
  name="${setting%%=*}"
  if ! sudo grep -q "^${name}=" /etc/fcv/fcv.env; then
    printf '%s\n' "$setting" | sudo tee -a /etc/fcv/fcv.env >/dev/null
  fi
done
for unit in "$repo"/deploy/systemd/fcv-*; do
  sudo install -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
done
sudo install -m 0755 "$repo/scripts/fcvctl" /usr/local/bin/fcvctl
sudo systemctl daemon-reload
sudo systemctl enable fcv-stack.target
echo 'Autostart installed and enabled. Review /etc/fcv/fcv.env, then run fcvctl start.'
