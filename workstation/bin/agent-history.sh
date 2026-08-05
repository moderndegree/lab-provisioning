#!/usr/bin/env bash
# What the agent chain actually did over the last 24h, across REAL sessions —
# the companion to agent-probe.sh, which only measures synthetic runs.
#
#   ./agent-history.sh
#
# Per orchestrator session: its own tool histogram and its child agents. Read it
# for the two regressions that matter and are otherwise invisible: `bash`/`edit`
# appearing in the orchestrator's own tools (it went back to doing the work),
# and a `kids` line missing the critics (it finished at coder).
#
# Runs ON ser5, where opencode lives.
set -uo pipefail
DB="$HOME/.local/share/opencode/opencode.db"
sqlite3 "$DB" "select id from session where (parent_id is null or parent_id='') and time_created > (strftime('%s','now')-86400)*1000 order by time_created desc limit 15;" | while read SID; do
  T=$(sqlite3 "$DB" "select datetime(time_created/1000,'unixepoch','localtime') from session where id='$SID';")
  TOOLS=$(sqlite3 "$DB" "select group_concat(t||':'||n,' ') from (select json_extract(data,'\$.tool') t, count(*) n from part where session_id='$SID' and json_extract(data,'\$.type')='tool' group by 1 order by n desc);")
  KIDS=$(sqlite3 "$DB" "select group_concat(agent||'x'||n,' ') from (select agent, count(*) n from session where parent_id='$SID' group by 1);")
  MSGS=$(sqlite3 "$DB" "select count(*) from message where session_id='$SID';")
  echo "$T  $SID  msgs=$MSGS"
  echo "   tools: ${TOOLS:-<none>}"
  echo "   kids : ${KIDS:-<none>}"
done
