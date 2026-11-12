#!/usr/bin/env bash
# Install system packages on a GitHub runner without letting a sick mirror eat the step limit.
#
#   apt_install.sh <package>...    install what is missing (nothing at all if all are present)
#   apt_install.sh --configure-only  write the same budget as an apt.conf.d drop-in, so apt calls we
#                                    cannot parametrise (``playwright install --with-deps``) obey it
#
# Why: the runner's mirror list names the Azure mirror first. When it resolves badly (about 20 s per
# name-resolution attempt, seen on 2026-11-11) every file apt requests is tried on that mirror first. With
# the old ``Acquire::Retries=3`` that was 4 attempts x 4 InRelease files x 20 s = 320 s, longer than the
# 5-minute step limit, so apt never reached the fallback mirror.
#
# How apt behaves (apt 2.7.14, the version on noble; source files in the Debian/apt repository):
#   * Retries are counted per file, not per mirror (apt-pkg/acquire-item.cc ``Item::Item`` reads
#     ``Acquire::Retries``; apt-pkg/acquire-worker.cc ``Worker::HandleFailure``). A transient failure
#     (``TmpResolveFailure`` included, acquire-worker.cc ``URI_FAILURE``) is repeated on the same mirror
#     until the counter is zero, only then the next mirror of the list is tried (``PopAlternativeURI``;
#     the list comes from the mirror method, methods/mirror.cc ``RedirectItem``, apt-transport-mirror(1)).
#   * A dead mirror is not remembered across files: methods/connect.cc caches only ``EAI_NONAME`` in
#     ``bad_addr``; ``EAI_AGAIN`` ("Temporary failure resolving", the 2026-11-11 symptom) is not cached. So
#     every InRelease and every .deb pays the full time again.
#   * ``Acquire::http::Timeout`` limits connect and stalled reads (apt.conf(5)); the libc resolver runs
#     before it and ignores it. One attempt therefore lasts max(resolution, Timeout).
#   * Index files (Packages, Components, ...) are fetched from the mirror that delivered their InRelease
#     (``Acquire::SameMirrorForAllIndexes``, default true; acquire-item.cc ``QueueURI`` and
#     ``CheckDownloadDone``). They cost nothing on a dead first mirror, so the option is pinned below.
#
# Budget (the workflows give the step 8 minutes = 480 s; tests/test_workflow_apt_guard.py recomputes it from
# these options and the real step limits, also with the resolution time doubled and 120 s kept free for the
# working mirror):
#   attempts per file                                       = Retries + 1 = 1
#   dead first mirror, update  = 4 InRelease x 1 x 20 s      =  80 s
#   dead first mirror, install = 4 .deb (2 packages + 1 dependency each) x 20 s = 80 s
#   worst case before the fallback mirror answers            = 160 s (resolution doubled: 320 s)
# This is a model from the apt sources, not a measurement; the 20 s figure comes from one incident log.
# A healthy run needs well under a minute, and none at all when the packages are already installed.
#
# ``--error-on=any`` makes a partial outage fail loudly instead of continuing with stale package lists.
# The runner's /etc/apt/apt-mirrors.txt is deliberately left alone: GitHub may change its format or
# location without notice.
set -euo pipefail

APT_OPTS=(-o Acquire::Retries=0 -o Acquire::http::Timeout=15 -o Acquire::https::Timeout=15
  -o Acquire::SameMirrorForAllIndexes=true)
SUDO="${APT_INSTALL_SUDO-sudo}"
CONF_DIR="${APT_CONF_DIR:-/etc/apt/apt.conf.d}"

if [ "${1:-}" = "--configure-only" ]; then
  $SUDO mkdir -p "$CONF_DIR"
  $SUDO tee "$CONF_DIR/99-ci-apt-budget" >/dev/null <<'CONF'
Acquire::Retries "0";
Acquire::http::Timeout "15";
Acquire::https::Timeout "15";
Acquire::SameMirrorForAllIndexes "true";
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
