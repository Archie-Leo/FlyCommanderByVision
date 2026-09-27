#!/usr/bin/env bash
set -Eeuo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ "$repo" == /home/lckfb/FlyCommanderByVision ]] || {
  echo "Install from /home/lckfb/FlyCommanderByVision, got $repo" >&2; exit 1;
}
sudo install -d -m 0755 /etc/fcv
if [[ ! -e /etc/fcv/fcv.env ]]; then
  sudo install -m 0644 "$repo/deploy/fcv/fcv.env.example" /etc/fcv/fcv.env
fi
for unit in "$repo"/deploy/systemd/fcv-*; do
  sudo install -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
done
sudo install -m 0755 "$repo/scripts/fcvctl" /usr/local/bin/fcvctl
sudo systemctl daemon-reload
sudo systemctl enable fcv-stack.target
echo 'Autostart installed and enabled. Review /etc/fcv/fcv.env, then run fcvctl start.'
