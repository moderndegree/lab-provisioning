#!/usr/bin/env bash
# Regression gate for the agent chain. Drives `opencode run` headlessly, then
# reads opencode's own sqlite db to report the dispatch TREE and whether the
# critic fan-out landed in a SINGLE assistant turn — that batching is the metric
# that matters, not the raw dispatch count.
#
#   ./agent-probe.sh "<prompt>" [timeout] [label]
#
# Run it after any change to opencode.json or .moderndegree/prompts/*. VERDICT:
# PASS requires all four critics distinct, qa having run, and zero forbidden
# tool calls by the orchestrator. See docs/agent-chain-findings.md for the
# measured results this produced and the traps baked in here (the $PREV session
# snapshot, `parent_id is null`, the `ru[n]` bracket trick, the stall watchdog).
#
# Runs ON ser5, where opencode lives — paths below assume that host.
set -uo pipefail
export PATH="$HOME/.npm-global/bin:$PATH"
DB="$HOME/.local/share/opencode/opencode.db"
LABEL="${3:-run}"
DIR=$HOME/agentprobe/$(date +%H%M%S)-$LABEL
mkdir -p "$DIR" && cd "$DIR"

PREV=$(sqlite3 "$DB" "select coalesce(max(time_created),0) from session;")
START=$(date +%s)
timeout "${2:-2700}" opencode run "$1" > "$DIR/out.txt" 2>&1 &
RUNPID=$!
# Stall watchdog: if no new part rows land for STALL seconds, the run is hung
# (opencode's bash tool blocks forever on a backgrounded server). Kill it so a
# hang costs minutes, not the full timeout.
STALL=${STALL:-360}
( LAST=0; SAME=0
  while kill -0 $RUNPID 2>/dev/null; do
    sleep 30
    N=$(sqlite3 "$DB" "select count(*) from part where time_created > $PREV;" 2>/dev/null || echo 0)
    if [ "$N" = "$LAST" ]; then SAME=$((SAME+30)); else SAME=0; LAST=$N; fi
    if [ "$SAME" -ge "$STALL" ]; then
      echo "!! STALLED: no new parts for ${SAME}s — killing" >> "$DIR/out.txt"
      pkill -f "opencode ru[n]"; break
    fi
  done ) &
WDPID=$!
wait $RUNPID
RC=$?
kill $WDPID 2>/dev/null
echo "=== label=$LABEL exit=$RC elapsed=$(( $(date +%s) - START ))s dir=$DIR"

SID=$(sqlite3 "$DB" "select id from session where (parent_id is null or parent_id='') and time_created > $PREV order by time_created desc limit 1;")
if [ -z "$SID" ]; then echo "!! no orchestrator session created"; tail -20 "$DIR/out.txt"; exit 1; fi
echo "=== orchestrator $SID"

echo "--- orchestrator tool histogram"
sqlite3 "$DB" "select '  '||tool||': '||n from (select coalesce(json_extract(data,'\$.tool'),'?') tool,count(*) n from part where session_id='$SID' and json_extract(data,'\$.type')='tool' group by 1) order by n desc;"

echo "--- forbidden tools used by orchestrator (want: none)"
sqlite3 "$DB" "select '  '||coalesce(json_extract(data,'\$.tool'),'?')||': '||count(*) from part where session_id='$SID' and json_extract(data,'\$.type')='tool' and json_extract(data,'\$.tool') in ('bash','edit','write','patch') group by json_extract(data,'\$.tool');" | grep . || echo "  none"

echo "--- dispatches (agent x count)"
sqlite3 "$DB" "select '  '||agent||' x'||n from (select agent,count(*) n from session where parent_id='$SID' group by 1) order by n desc;" | grep . || echo "  NONE"

echo "--- task calls grouped by assistant turn (batch size = parallelism)"
sqlite3 "$DB" "
 select '  turn '||row_number() over (order by mn)||': ['||n||'] '||agents from (
   select min(time_created) mn, count(*) n,
          group_concat(json_extract(data,'\$.state.input.subagent_type'),', ') agents
   from part where session_id='$SID' and json_extract(data,'\$.tool')='task'
   group by message_id) order by mn;"

echo "--- cortex usage (orchestrator + all subagents)"
sqlite3 "$DB" "
 select '  '||tool||': '||n from (
   select json_extract(p.data,'\$.tool') tool, count(*) n
   from part p join session s on s.id=p.session_id
   where (s.id='$SID' or s.parent_id='$SID')
     and json_extract(p.data,'\$.tool') like 'cortex%'
   group by 1) order by n desc;" | grep . || echo "  NONE"
CORTEX=$(sqlite3 "$DB" "select count(*) from part p join session s on s.id=p.session_id where (s.id='$SID' or s.parent_id='$SID') and json_extract(p.data,'\$.tool') like 'cortex%';")
CAP=$(sqlite3 "$DB" "select count(*) from part p join session s on s.id=p.session_id where (s.id='$SID' or s.parent_id='$SID') and json_extract(p.data,'\$.tool') like '%capture%';")
CRIT=$(sqlite3 "$DB" "select count(distinct agent) from session where parent_id='$SID' and agent in ('reviewer','security-auditor','tester','doc-writer');")
BATCH=$(sqlite3 "$DB" "select coalesce(max(n),0) from (select count(*) n from part where session_id='$SID' and json_extract(data,'\$.tool')='task' group by message_id);")
QA=$(sqlite3 "$DB" "select count(*) from session where parent_id='$SID' and agent='qa';")
BAD=$(sqlite3 "$DB" "select count(*) from part where session_id='$SID' and json_extract(data,'\$.type')='tool' and json_extract(data,'\$.tool') in ('bash','edit','write','patch');")
# Tools still marked `running` after the run ended = something hung. Counted
# BEFORE the verdict, because a hang has to be able to fail the run.
STUCK=$(sqlite3 "$DB" "select count(*) from part p join session s on s.id=p.session_id where (s.id='$SID' or s.parent_id='$SID') and json_extract(p.data,'\$.state.status')='running';")

echo "=== SCORE  distinct_critics=$CRIT/4  max_task_batch=$BATCH  qa=$QA  forbidden_tool_calls=$BAD  cortex_calls=$CORTEX  captures=$CAP  stuck_tools=$STUCK  exit=$RC"

# The dispatch criteria alone are not enough. Measured 2026-08-05: run
# `process1` dispatched all four critics, ran qa, called no forbidden tool — and
# scored PASS despite being KILLED by the stall watchdog after 34 minutes with a
# tester still holding a backgrounded server. A chain that delegates perfectly
# and then hangs has not passed anything, so the exit code and the stuck-tool
# count are part of the verdict now.
if [ "$CRIT" = 4 ] && [ "$QA" -ge 1 ] && [ "$BAD" = 0 ] && [ "$RC" = 0 ] && [ "$STUCK" = 0 ]; then
  VERDICT=PASS
elif [ "$RC" != 0 ] || [ "$STUCK" != 0 ]; then
  VERDICT=STALLED
else
  VERDICT=FAIL
fi
echo "=== VERDICT: $VERDICT"

# Persist the score. The original version printed it to stdout only, so the run
# tally in docs/agent-chain-findings.md survives nowhere but a terminal
# scrollback — the numbers could not be re-derived from the run dirs afterwards.
# One TSV line per run, appended, so a tally is `sort | uniq -c` away.
RESULTS="$HOME/agentprobe/results.tsv"
[ -s "$RESULTS" ] || printf 'when\tlabel\tverdict\tcritics\tbatch\tqa\tforbidden\tcortex\tcaptures\tstuck\texit\telapsed_s\tsession\n' > "$RESULTS"
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "$(date -Is)" "$LABEL" "$VERDICT" "$CRIT" "$BATCH" "$QA" "$BAD" "$CORTEX" "$CAP" \
  "$STUCK" "$RC" "$(( $(date +%s) - START ))" "$SID" >> "$RESULTS"

echo "--- tools left RUNNING (hang evidence)"
sqlite3 "$DB" "select '  '||s.agent||' | '||json_extract(p.data,'\$.tool')||' | '||substr(replace(coalesce(json_extract(p.data,'\$.state.input.command'),json_extract(p.data,'\$.state.input.filePath'),''),char(10),' '),1,80) from part p join session s on s.id=p.session_id where (s.id='$SID' or s.parent_id='$SID') and json_extract(p.data,'\$.state.status')='running';" | grep . || echo "  none"

echo "=== files produced"; find "$DIR" -type f -not -name out.txt | head -10 | sed 's/^/  /'
echo "=== tail"; tail -8 "$DIR/out.txt" | sed 's/^/  /'
