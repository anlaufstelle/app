#!/usr/bin/env bash
# Install system packages on a GitHub runner without letting a sick mirror eat the step limit.
#
#   apt_install.sh <package>...    install what is missing (nothing at all if all are present)
#   apt_install.sh --configure-only  write the same budget as an apt.conf.d drop-in, so apt calls we
#                                    cannot parametrise (``playwright install --with-deps``) obey it
#
# Why: the runner's mirror list names the Azure mirror first. When it resolves badly (about 20 s per
# name-resolution attempt, seen on 2026-11-11) every index file is tried on that mirror first. With the
# old ``Acquire::Retries=3`` that was 4 attempts x 4 InRelease files x 20 s = 320 s, longer than the
# 5-minute step limit, so apt never reached the fallback mirror.
#
# Budget (the workflows give the step 8 minutes = 480 s; tests/test_workflow_apt_guard.py recomputes it):
#   attempts per file         = Retries + 1                    = 2
#   update, dead first mirror = 2 x 4 InRelease x 20 s         = 160 s
#   install, dead first mirror= 2 x <=3 .deb files x 20 s      = 120 s
#   worst case before the fallback mirror answers              = 280 s  (58 % of the limit)
# A healthy run needs well under a minute, and none at all when the packages are already installed.
#
# ``--error-on=any`` makes a partial outage fail loudly instead of continuing with stale package lists.
# The runner's /etc/apt/apt-mirrors.txt is deliberately left alone: GitHub may change its format or
# location without notice.
set -euo pipefail

APT_OPTS=(-o Acquire::Retries=1 -o Acquire::http::Timeout=15 -o Acquire::https::Timeout=15)
SUDO="${APT_INSTALL_SUDO-sudo}"
CONF_DIR="${APT_CONF_DIR:-/etc/apt/apt.conf.d}"

if [ "${1:-}" = "--configure-only" ]; then
  $SUDO mkdir -p "$CONF_DIR"
  $SUDO tee "$CONF_DIR/99-ci-apt-budget" >/dev/null <<'CONF'
Acquire::Retries "1";
Acquire::http::Timeout "15";
Acquire::https::Timeout "15";
CONF
  exit 0
fi

if [ "$#" -eq 0 ]; then
  echo "usage: apt_install.sh <package>... | --configure-only" >&2
  exit 2
fi

missing=()
for package in "$@"; do
  if [ "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)" != "install ok installed" ]; then
    missing+=("$package")
  fi
done

if [ "${#missing[@]}" -eq 0 ]; then
  echo "apt_install: already installed ($*); skipping apt"
  exit 0
fi

echo "apt_install: installing ${missing[*]}"
$SUDO apt-get "${APT_OPTS[@]}" update --error-on=any
$SUDO apt-get "${APT_OPTS[@]}" install -y --no-install-recommends "${missing[@]}"
