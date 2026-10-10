#!/bin/bash
# Watch a do-release-upgrade running in root tmux session "upgrade" on <host>.
# Run on the workstation, in the background:  bash watch.sh <ssh-host> [interval-seconds]
# Exits 0 with "PROMPT: <line>" when the pane's last visible line looks like a prompt and is unchanged
# across two polls, or "UPGRADER EXITED" when the process is gone. Prints one status line per poll.
host=${1:?usage: watch.sh <ssh-host> [interval]}; every=${2:-15}
probe='S(){ for c in /usr/bin/sudo /usr/lib/cargo/bin/sudo /usr/bin/sudo.ws; do [ -x "$c" ] && { "$c" -n "$@"; return; }; done; }
alive=$(pgrep -f "dist-upgrade|do-release-upgrade" >/dev/null && echo ALIVE || echo DEAD)
iu=$(dpkg -l 2>/dev/null | awk "NR>5 && \$1==\"iU\"" | wc -l)
last=$(S tmux capture-pane -p -t upgrade | grep -v "^\s*$" | tail -1)
echo "$alive|$iu|$last"'
prev=""
while true; do
  out=$(ssh -o ConnectTimeout=8 -o BatchMode=yes "$host" 'bash -s' <<<"$probe" 2>/dev/null)
  if [ -z "$out" ]; then echo "$(date +%T) ssh unreachable (expected while openssh-server is replaced)"; sleep 20; continue; fi
  echo "$(date +%T) $out"
  case "$out" in DEAD*) echo "UPGRADER EXITED"; exit 0;; esac
  last=${out#*|*|}
  if [ "$last" = "$prev" ] && printf %s "$last" | grep -qE '\[[yYnN]|\[Y/I/N|Continue|press|ENTER|\?\s*$|\]\s*$|:\s*$'; then
    echo "PROMPT: $last"; exit 0
  fi
  prev=$last; sleep "$every"
done
