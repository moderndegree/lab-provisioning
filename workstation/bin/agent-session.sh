#!/usr/bin/env bash
# Post-hoc breakdown of ONE session, by id. Use it on a session that looked
# wrong in agent-history.sh, or on any real (non-probe) run.
#
#   ./agent-session.sh <session_id>
#
# Session ids come from agent-history.sh. Scores the same criteria as
# agent-probe.sh, so a real session can be judged against the same bar as a
# synthetic one. Runs ON ser5, where opencode lives.
set -uo pipefail
DB="$HOME/.local/share/opencode/opencode.db"; SID="${1:-}"
if [ -z "$SID" ]; then echo "usage: $0 <session_id>   (list ids with agent-history.sh)" >&2; exit 2; fi
echo "--- tools"; sqlite3 "$DB" "select '  '||tool||': '||n from (select coalesce(json_extract(data,'\$.tool'),'?') tool,count(*) n from part where session_id='$SID' and json_extract(data,'\$.type')='tool' group by 1) order by n desc;"
echo "--- dispatches"; sqlite3 "$DB" "select '  '||agent||' x'||n from (select agent,count(*) n from session where parent_id='$SID' group by 1);" | grep . || echo "  NONE"
echo "--- task calls per assistant turn"
sqlite3 "$DB" "select '  ['||n||'] '||agents from (select min(time_created) mn,count(*) n, group_concat(json_extract(data,'\$.state.input.subagent_type'),', ') agents from part where session_id='$SID' and json_extract(data,'\$.tool')='task' group by message_id) order by mn;"
CRIT=$(sqlite3 "$DB" "select count(distinct agent) from session where parent_id='$SID' and agent in ('reviewer','security-auditor','tester','doc-writer');")
BATCH=$(sqlite3 "$DB" "select coalesce(max(n),0) from (select count(*) n from part where session_id='$SID' and json_extract(data,'\$.tool')='task' group by message_id);")
QA=$(sqlite3 "$DB" "select count(*) from session where parent_id='$SID' and agent='qa';")
BAD=$(sqlite3 "$DB" "select count(*) from part where session_id='$SID' and json_extract(data,'\$.type')='tool' and json_extract(data,'\$.tool') in ('bash','edit','write','patch');")
echo "=== SCORE critics=$CRIT/4 max_batch=$BATCH qa=$QA forbidden=$BAD"
