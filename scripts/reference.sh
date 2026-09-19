#!/usr/bin/env bash
# The reference a v2 commit is measured against (DESIGN.md §14).
#
#   scripts/reference.sh snapshot DIR   copy the cache swage reads from into DIR
#   scripts/reference.sh replay DIR     `swage audit --all --cached` against DIR
#
# A `--cached` audit replays the GitHub reads the last live sweep recorded,
# but three other caches feed a plan and each can move underneath it: the
# upstream archives, the name index -- rewritten in place after a 24 h TTL
# -- and the dynamic-metadata builds. A snapshot copies all of them so that
# every difference between two replays is the code's, and `replay` points
# swage at the copy through XDG_CACHE_HOME and refreshes the index's mtimes
# so the TTL never expires inside it.
#
# The archives are hardlinked rather than copied: they are 2 GB, keyed by
# URL, and swage writes a new one through a temporary file and a rename, so
# a link in the snapshot keeps the bytes the snapshot was taken with.
set -euo pipefail

usage() { sed -n '2,5p' "$0" >&2; exit 2; }

command=${1:-}
target=${2:-}
[[ -n $command && -n $target ]] || usage

source_root=${XDG_CACHE_HOME:-$HOME/.cache}/swage
snapshot_root=$target/swage

case $command in
  snapshot)
    [[ -e $snapshot_root ]] && { echo "$snapshot_root exists; a reference is never overwritten" >&2; exit 1; }
    mkdir -p "$snapshot_root"
    for part in reads index names dyn-archives; do
      [[ -d $source_root/$part ]] && cp -r "$source_root/$part" "$snapshot_root/$part"
    done
    [[ -d $source_root/archives ]] && cp -al "$source_root/archives" "$snapshot_root/archives"
    du -sh "$snapshot_root"/* | sed 's/^/  /'
    echo "snapshot at $snapshot_root"
    ;;
  replay)
    [[ -d $snapshot_root/reads ]] || { echo "$snapshot_root/reads is missing; snapshot first" >&2; exit 1; }
    shift 2
    touch "$snapshot_root"/index/* 2>/dev/null || true
    XDG_CACHE_HOME=$target exec swage audit --all --cached "$@"
    ;;
  *)
    usage
    ;;
esac
