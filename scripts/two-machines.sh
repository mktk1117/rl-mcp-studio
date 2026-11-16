#!/usr/bin/env bash
# Two machines on one desktop: a container running `rlmcp hostd` stands in for
# a training machine, and a scratch studio from this checkout launches on it.
#
# The worker is the Dockerfile's `train` target (built first if $IMAGE does not
# exist). It has its own filesystem, its own Python and the GPU passed through;
# nothing from the task repository exists inside it. The studio pushes the
# stamped code tree in, the worker trains from that copy, and the record here
# is filled from what the worker reports.
#
# Needs a development checkout (rl-mcp beside this repository, see
# CONTRIBUTING.md), podman or docker with GPU passthrough, and:
#
#   TASKS        a task repository, ideally a git repository so the launch has
#                a code tree to push. A scratch records root is created inside
#                it for the run and removed afterwards.
#
# Optional: TASK (default Mjlab-Cartpole-Balance), IMAGE, CONTAINER, SCRATCH,
# WORKER_PORT (default 8760), STUDIO_PORT (default 8794).
#
#   TASKS=/path/to/tasks scripts/two-machines.sh

set -u
cd "$(dirname "$0")/.."

: "${TASKS:?set TASKS to a task repository (see the header of this script)}"
[ -d "$TASKS" ] || { echo "TASKS=$TASKS is not a directory" >&2; exit 2; }
TASKS="$(cd "$TASKS" && pwd)"
CONTAINER="${CONTAINER:-$(command -v podman || command -v docker || true)}"
[ -n "$CONTAINER" ] || { echo "no container engine on PATH (podman or docker)" >&2; exit 2; }
TASK="${TASK:-Mjlab-Cartpole-Balance}"
IMAGE="${IMAGE:-rlmcp-studio:train}"
S="${SCRATCH:-${TMPDIR:-/tmp}/rlmcp-two-machines}"
WORKER_PORT="${WORKER_PORT:-8760}"
STUDIO_PORT="${STUDIO_PORT:-8794}"
HOSTTOK="worker-$RANDOM$RANDOM"
STUDIO_TOK="studio-$RANDOM$RANDOM"
RECORDS="$TASKS/records-two-machines"
NAME="rlmcp-worker-$$"
STUDIO="http://127.0.0.1:$STUDIO_PORT"
WORKER="http://127.0.0.1:$WORKER_PORT"

if [ -e /etc/cdi/nvidia.yaml ] || [ -e /var/run/cdi/nvidia.yaml ]; then
  DEVICE=(--device nvidia.com/gpu=all)
else
  DEVICE=(--gpus all)
fi

cleanup() {
  [ -f "$S/studio.pid" ] && kill "$(cat "$S/studio.pid")" 2>/dev/null
  "$CONTAINER" rm -f "$NAME" >/dev/null 2>&1
  rm -rf "$RECORDS"
}
trap cleanup EXIT

rm -rf "$S"; mkdir -p "$S/xdg/state" "$S/xdg/config/rlmcp-studio" "$RECORDS"

if ! "$CONTAINER" image exists "$IMAGE" 2>/dev/null && ! "$CONTAINER" image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "== building $IMAGE (the Dockerfile's train target)"
  "$CONTAINER" build --target train -t "$IMAGE" . >"$S/build.log" 2>&1 \
    || { echo "image build failed; see $S/build.log" >&2; exit 1; }
fi

echo "== worker: rlmcp hostd in a container"
"$CONTAINER" run -d --rm --name "$NAME" "${DEVICE[@]}" -p "127.0.0.1:$WORKER_PORT:8740" \
  --entrypoint rlmcp "$IMAGE" hostd --root /home/studio/hostlogs --bind 0.0.0.0 --port 8740 \
  --token "$HOSTTOK" >/dev/null
for _ in $(seq 1 40); do
  curl -sf -H "authorization: Bearer $HOSTTOK" -o /dev/null "$WORKER/v1/host" && break; sleep 0.5
done
curl -s -H "authorization: Bearer $HOSTTOK" "$WORKER/v1/host" | python3 -c '
import sys, json
d = json.load(sys.stdin)
print("worker:", d["host_id"], "gpus", [g["name"] for g in d["gpus"]], "root", d["root"])'

# The host, written down: the container's interpreter and paths.
PY=$("$CONTAINER" exec "$NAME" sh -c 'echo $(uv tool dir)/rlmcp-studio/bin/python')
cat > "$S/xdg/config/rlmcp-studio/settings.json" <<JSON
{"hosts": [{"name": "worker", "url": "$WORKER", "token": "$HOSTTOK",
            "python": "$PY", "cwd": "/home/studio/work", "log_root": "/home/studio/hostlogs"}]}
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
print("capacity seen by the studio:", [(c["id"], c["label"]) for c in json.load(sys.stdin)["capacity"]])'

echo "== launch $TASK on the worker"
L=$(curl -s "${A[@]}" -X POST -H 'content-type: application/json' \
  -d "{\"task\":\"$TASK\",\"slug\":\"on-the-worker\",\"hypothesis\":\"a worker with its own filesystem trains from the pushed tree\",\"num_envs\":256,\"max_iterations\":5,\"host\":\"worker\"}" \
  "$STUDIO/api/build/launch")
printf '%s' "$L" | python3 -c '
import sys, json
d = json.load(sys.stdin); j = d.get("job", {})
print("launch:", d.get("ok"), d.get("record"), j.get("runner"), "|", (j.get("note") or d.get("error") or "")[:140])'
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
echo "on the worker, the pushed trees:"
"$CONTAINER" exec "$NAME" sh -c 'ls /home/studio/hostlogs/.rlmcp-hostd/trees/ 2>/dev/null'
[ "$ST" = succeeded ]
