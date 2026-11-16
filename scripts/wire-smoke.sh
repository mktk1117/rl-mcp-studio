#!/usr/bin/env bash
# One machine, over the wire: `rlmcp hostd` and a scratch studio on this
# machine, and a launch that goes through the host rather than straight to a
# local subprocess.
#
# Needs a development checkout (rl-mcp beside this repository, see
# CONTRIBUTING.md), a GPU, and:
#
#   TASKS        a task repository with its own `.venv` that has rl-mcp (with
#                its `server` and `mjlab` extras) installed; ideally a git
#                repository, so the launch has a code tree to push. A scratch
#                records root is created inside it and removed afterwards.
#
# Optional: TASK (default Mjlab-Cartpole-Balance), SCRATCH, HOSTD_PORT
# (default 8749), STUDIO_PORT (default 8795). MUJOCO_MENAGERIE is passed
# through if the task needs it.
#
#   TASKS=/path/to/tasks scripts/wire-smoke.sh

set -u
cd "$(dirname "$0")/.."

: "${TASKS:?set TASKS to a task repository with a .venv (see the header of this script)}"
[ -d "$TASKS" ] || { echo "TASKS=$TASKS is not a directory" >&2; exit 2; }
TASKS="$(cd "$TASKS" && pwd)"
TASK_PY="$TASKS/.venv/bin/python"
[ -x "$TASK_PY" ] || { echo "no interpreter at $TASK_PY" >&2; exit 2; }
TASK="${TASK:-Mjlab-Cartpole-Balance}"
S="${SCRATCH:-${TMPDIR:-/tmp}/rlmcp-wire-smoke}"
HOSTD_PORT="${HOSTD_PORT:-8749}"
STUDIO_PORT="${STUDIO_PORT:-8795}"
TOK="wire-$RANDOM$RANDOM"
STUDIO_TOK="studio-$RANDOM$RANDOM"
RECORDS="$TASKS/records-wire-smoke"
STUDIO="http://127.0.0.1:$STUDIO_PORT"

cleanup() {
  for pid in "$S/studio.pid" "$S/hostd.pid"; do
    [ -f "$pid" ] && kill "$(cat "$pid")" 2>/dev/null
  done
  rm -rf "$RECORDS"
}
trap cleanup EXIT

rm -rf "$S"; mkdir -p "$S/hostlogs" "$S/xdg/state" "$S/xdg/config/rlmcp-studio" "$RECORDS"

echo "== hostd, from the task repository's interpreter"
"$TASK_PY" -m rlmcp.hostd --root "$S/hostlogs" --port "$HOSTD_PORT" --token "$TOK" >"$S/hostd.log" 2>&1 &
echo $! > "$S/hostd.pid"

cat > "$S/xdg/config/rlmcp-studio/settings.json" <<JSON
{"hosts": [{"name": "here", "url": "http://127.0.0.1:$HOSTD_PORT", "token": "$TOK",
            "python": "$TASK_PY", "cwd": "$TASKS", "log_root": "$S/hostlogs"}]}
JSON

echo "== studio: a scratch one from this checkout"
XDG_STATE_HOME="$S/xdg/state" XDG_CONFIG_HOME="$S/xdg/config" RLMCP_STUDIO_TOKEN="$STUDIO_TOK" \
  env -u RLMCP_RECORDS -u RLMCP_TASK_PACKAGES \
  uv run rlmcp-studio --records "$RECORDS" --port "$STUDIO_PORT" >"$S/studio.log" 2>&1 &
echo $! > "$S/studio.pid"
A=(-H "authorization: Bearer $STUDIO_TOK")
for _ in $(seq 1 60); do curl -sf "${A[@]}" -o /dev/null "$STUDIO/api/config" && break; sleep 0.5; done
curl -s "${A[@]}" "$STUDIO/api/build/launches" | python3 -c '
import sys, json
print("hosts known to the studio:", [h["name"] for h in json.load(sys.stdin)["hosts"]])'

echo "== launch $TASK through the host"
L=$(curl -s "${A[@]}" -X POST -H 'content-type: application/json' \
  -d "{\"task\":\"$TASK\",\"slug\":\"over-the-wire\",\"hypothesis\":\"a launch through a local hostd trains from the pushed tree\",\"num_envs\":256,\"max_iterations\":5,\"host\":\"here\"}" \
  "$STUDIO/api/build/launch")
printf '%s' "$L" | python3 -c '
import sys, json
d = json.load(sys.stdin); j = d.get("job", {})
print("launch:", d.get("ok"), d.get("record"), j.get("runner"), "|", (j.get("note") or d.get("error") or "")[:120])'
JOB=$(printf '%s' "$L" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("job",{}).get("id",""))')
REC=$(printf '%s' "$L" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("record",""))')
[ -n "$JOB" ] || { echo "launch refused" >&2; exit 1; }

ST=""
for _ in $(seq 1 240); do
  ST=$(curl -s "${A[@]}" "$STUDIO/api/build/launches?tail=5" | python3 -c "
import sys, json
j = [x for x in json.load(sys.stdin)['jobs'] if x['id'] == '$JOB']
print(j[0]['state'] if j else 'gone')")
  case "$ST" in succeeded|failed|cancelled|gone) break ;; esac; sleep 5
done
echo "job ended: $ST"
curl -s "${A[@]}" "$STUDIO/api/build/launches?tail=8" | python3 -c "
import sys, json
j = [x for x in json.load(sys.stdin)['jobs'] if x['id'] == '$JOB'][0]
print('session:', j['session_dir']); print('workdir:', j['workdir']); print('note:', j['note'])
print('log tail:'); print('\n'.join('   ' + l for l in j['log'][-6:]))"
uv run python - <<PY
from rlmcp.records import open_store
r = open_store("$RECORDS").get_record("$REC")
print("record:", r.id, "verdict:", r.verdict, "session:", r.session)
print("config keys:", len(r.config or {}), "code tree:", (r.code or {}).get("tree", "")[:12])
PY
echo "hostd log tail:"; tail -3 "$S/hostd.log"
[ "$ST" = succeeded ]
