#!/bin/bash
# Host preparation only. Contains NO secrets: .env and the credentials file are created on the
# host by the operator through an SSM session (see README.md in this directory).
set -euo pipefail

dnf -y update
dnf -y install docker git
systemctl enable --now docker

# Docker Compose v2 plugin (pinned release).
COMPOSE_VERSION="v2.40.3"
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod 0755 /usr/local/lib/docker/cli-plugins/docker-compose

# Encrypted data volume: format once, mount at /srv/procurelens, keep Docker data on it.
DEV=""
for _ in $(seq 1 30); do
  for cand in /dev/nvme1n1 /dev/xvdf /dev/sdf; do
    [ -b "$cand" ] && DEV="$cand" && break 2
  done
  sleep 5
done
if [ -n "$DEV" ]; then
  blkid "$DEV" >/dev/null 2>&1 || mkfs.xfs "$DEV"
  mkdir -p /srv/procurelens
  UUID=$(blkid -s UUID -o value "$DEV")
  grep -q "$UUID" /etc/fstab || echo "UUID=$UUID /srv/procurelens xfs defaults,nofail 0 2" >> /etc/fstab
  mount -a
  systemctl stop docker
  mkdir -p /srv/procurelens/docker
  echo '{"data-root": "/srv/procurelens/docker"}' > /etc/docker/daemon.json
  systemctl start docker
  install -d -m 0700 -o 10001 -g 10001 /srv/procurelens/private
  install -d -m 0750 /srv/procurelens/backups
fi
