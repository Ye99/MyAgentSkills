#!/bin/bash
# Verify a ZFS replication between two hosts: snapshot GUIDs, then metadata + SHA-256 of every path that
# changed between two snapshots, read from the immutable .zfs/snapshot directories on both sides.
#
#   bash verify.sh <src-host> <src-dataset> <dst-host> <dst-dataset> <from-snap> <to-snap>
#
# Run from any machine with passwordless ssh (and passwordless sudo) to both hosts; run it under nohup/tmux,
# because hashing takes as long as reading the changed data on the slower host. The hashing jobs themselves
# run detached on each host in ~/zfs-verify-<to-snap>/, so a dropped connection only stops the polling:
# rerun the same command and it resumes waiting (it never restarts a finished or running job).
# Env: THREADS (default 4) parallel files per host; POLL (default 60) seconds between checks.
set -euo pipefail
[ $# -eq 6 ] || { sed -n '2,12p' "$0"; exit 2; }
SH=$1 SD=$2 DH=$3 DD=$4 FROM=$5 TO=$6
THREADS=${THREADS:-4} POLL=${POLL:-60}
HERE=$(cd "$(dirname "$0")" && pwd)
W="zfs-verify-$TO"                      # work dir under each host's home
LOCAL=$(mktemp -d)
# Run a command on a host under bash, whatever the login shell is (zsh does not word-split, and errors on
# unmatched globs). The command text arrives on stdin, so it never appears in a process list either.
r(){ local h=$1; shift; ssh -o ServerAliveInterval=30 "$h" bash -s <<<"$*"; }

echo "== 1. snapshot names and GUIDs"
r "$SH" "zfs list -H -t snapshot -d 1 -o name,guid -s createtxg $SD" | sed 's/^[^@]*@//' > "$LOCAL/src.guid"
r "$DH" "zfs list -H -t snapshot -d 1 -o name,guid -s createtxg $DD" | sed 's/^[^@]*@//' > "$LOCAL/dst.guid"
for s in "$FROM" "$TO"; do
  grep -q "^$s	" "$LOCAL/src.guid" || { echo "FAIL: @$s missing on source"; exit 1; }
  grep -q "^$s	" "$LOCAL/dst.guid" || { echo "FAIL: @$s missing on target"; exit 1; }
done
# every target snapshot must exist on the source with the same GUID (the target may keep fewer)
if ! awk -F'\t' 'NR==FNR{g[$1]=$2; next} !($1 in g) || g[$1]!=$2 {print "  mismatch: " $0; bad=1} END{exit bad}' \
     "$LOCAL/src.guid" "$LOCAL/dst.guid"; then echo "FAIL: snapshot GUIDs differ"; exit 1; fi
echo "OK: all $(wc -l < "$LOCAL/dst.guid") target snapshots match the source by name and GUID"
tok=$(r "$DH" "zfs get -H -o value receive_resume_token $DD")
[ "$tok" = - ] || echo "WARNING: target has a partial receive (resume token); the snapshots compared are complete regardless"

SM=$(r "$SH" "zfs get -H -o value mountpoint $SD"); DM=$(r "$DH" "zfs get -H -o value mountpoint $DD")
case "$SM$DM" in *legacy*|*none*) echo "FAIL: dataset needs a real mountpoint for .zfs/snapshot access"; exit 1;; esac

echo "== 2. changed paths between @$FROM and @$TO (source)"
r "$SH" "mkdir -p ~/$W"; r "$DH" "mkdir -p ~/$W"
for h in "$SH" "$DH"; do scp -q "$HERE/manifest.py" "$HERE/changed_paths.py" "$h:$W/"; done
if ! r "$SH" "test -s ~/$W/present.nul"; then
  r "$SH" "cd ~/$W && sudo -n zfs diff -FH $SD@$FROM $SD@$TO > zfsdiff.txt && python3 -I changed_paths.py '$SM' . < zfsdiff.txt"
fi
for f in present.nul deleted.nul; do ssh "$SH" "cat ~/$W/$f" | ssh "$DH" "cat > ~/$W/$f"; done
a=$(r "$SH" "cd ~/$W && sha256sum present.nul deleted.nul manifest.py | cut -d' ' -f1 | xargs")
b=$(r "$DH" "cd ~/$W && sha256sum present.nul deleted.nul manifest.py | cut -d' ' -f1 | xargs")
[ "$a" = "$b" ] || { echo "FAIL: inputs differ between hosts"; exit 1; }
echo "OK: identical path lists and script on both hosts"

echo "== 3. manifests (detached on each host, $THREADS files at a time)"
# Job state per host: <side>.done when finished, <side>.pid while running. Never restarts a done or live job.
start(){ # host side mount
  r "$1" "cd ~/$W
    [ -f $2.done ] && exit 0
    [ -f $2.pid ] && kill -0 \$(cat $2.pid) 2>/dev/null && exit 0
    S=$3/.zfs/snapshot/$TO
    setsid nohup bash -c \"sudo -n python3 -I manifest.py \$S present.nul $2-present.tsv $THREADS && \
      sudo -n python3 -I manifest.py \$S deleted.nul $2-deleted.tsv 2 && touch $2.done\" > $2.log 2>&1 < /dev/null &
    echo \$! > $2.pid"
}
state(){ # host side -> done | running | dead
  r "$1" "cd ~/$W; if [ -f $2.done ]; then echo done; elif kill -0 \$(cat $2.pid 2>/dev/null) 2>/dev/null; then echo running; else echo dead; fi"
}
start "$SH" src "$SM"; start "$DH" dst "$DM"
while :; do
  a=$(state "$SH" src); b=$(state "$DH" dst)
  [ "$a$b" = donedone ] && break
  for x in "$SH src $a" "$DH dst $b"; do
    set -- $x
    [ "$3" = dead ] && { echo "FAIL: manifest job on $1 stopped without finishing:"; r "$1" "tail -5 ~/$W/$2.log"; exit 1; }
  done
  sleep "$POLL"
done

echo "== 4. compare"
scp -q "$SH:$W/src-present.tsv" "$LOCAL/"
scp -q "$DH:$W/dst-present.tsv" "$DH:$W/dst-deleted.tsv" "$LOCAL/"
python3 -I "$HERE/compare.py" "$LOCAL/src-present.tsv" "$LOCAL/dst-present.tsv" "$LOCAL/dst-deleted.tsv"
echo "Results kept in $LOCAL and ~/$W on both hosts."
