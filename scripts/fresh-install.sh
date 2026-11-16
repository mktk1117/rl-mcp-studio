#!/usr/bin/env bash
# A fresh install, end to end, in a container.
#
# Builds the wheel from this checkout, installs it into an empty image the way
# the README says to, and then asks the install the questions a new user
# would: does `--check` pass, does the server come up, does the page load,
# does `/api/doctor` report the machine honestly. With `--gpu` it also asks
# whether a CUDA device is visible inside the container, which a hosted CI
# runner cannot answer.
#
#   scripts/fresh-install.sh           # build + check + serve + probe
#   scripts/fresh-install.sh --gpu     # same, with the GPU passed through
#   scripts/fresh-install.sh --train   # the `train` image: mjlab installed,
#                                      # tasks listed, one short run launched
#                                      # through the studio's own API (implies --gpu)
#   scripts/fresh-install.sh --shell   # drop into the container afterwards
#
# podman or docker, whichever is on PATH (or $CONTAINER=…). Exit status is
# the number of probes that failed, so it works as a test.

set -u
cd "$(dirname "$0")/.."

CONTAINER="${CONTAINER:-$(command -v podman || command -v docker || true)}"
PORT="${PORT:-8731}"
GPU=0; SHELL_AFTER=0; TRAIN=0
for arg in "$@"; do
  case "$arg" in
    --gpu) GPU=1 ;;
    --train) TRAIN=1; GPU=1 ;;
    --shell) SHELL_AFTER=1 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done
TARGET=fresh; [ "$TRAIN" = 1 ] && TARGET=train
IMAGE="${IMAGE:-rlmcp-studio:$TARGET}"
# The task the training probe launches: mjlab's lightest built-in, so the
# claim being tested is "the install trains", not "the GPU is fast".
TASK="${TASK:-Mjlab-Cartpole-Balance}"
# The studio guards /api (guard.py). The container gets a token in its
# environment and every probe presents it as a bearer token.
TOKEN="${RLMCP_STUDIO_TOKEN:-$(head -c 24 /dev/urandom | base64 | tr -d '/+=')}"
AUTH=(-H "authorization: Bearer $TOKEN")
if [ -z "$CONTAINER" ]; then
  echo "no container engine on PATH (podman or docker)" >&2
  exit 2
fi

DEVICE=()
if [ "$GPU" = 1 ]; then
  # CDI is how podman (and recent docker) name a GPU; the legacy --gpus flag
  # is docker-only. Prefer CDI when the spec exists, fall back otherwise.
  if [ -e /etc/cdi/nvidia.yaml ] || [ -e /var/run/cdi/nvidia.yaml ]; then
    DEVICE=(--device nvidia.com/gpu=all)
  else
    DEVICE=(--gpus all)
  fi
fi

failed=0
pass() { printf '  ok   %s\n' "$1"; }
fail() { printf '  FAIL %s\n' "$1"; failed=$((failed + 1)); }

echo "== build  ($CONTAINER, $IMAGE)"
# The build log is long (every wheel uv installs) and only interesting when
# the build fails, so it goes to a file and is shown then.
BUILD_LOG="${TMPDIR:-/tmp}/rlmcp-fresh-build-$$.log"
if ! "$CONTAINER" build --target "$TARGET" -t "$IMAGE" . >"$BUILD_LOG" 2>&1; then
  echo "image build failed; last lines of $BUILD_LOG:" >&2; tail -30 "$BUILD_LOG" >&2; exit 1
fi
rm -f "$BUILD_LOG"

echo "== rlmcp-studio --check  (inside the container)"
if "$CONTAINER" run --rm "${DEVICE[@]}" "$IMAGE" --check; then
  pass "--check exits 0: the wheel carried what it needs"
else
  fail "--check exits non-zero: a required item is missing from the install"
fi

echo "== serve"
NAME="rlmcp-fresh-$$"
"$CONTAINER" run -d --rm --name "$NAME" -p "127.0.0.1:${PORT}:8730" -e "RLMCP_STUDIO_TOKEN=$TOKEN" "${DEVICE[@]}" "$IMAGE" \
  --host 0.0.0.0 --port 8730 >/dev/null
trap '"$CONTAINER" rm -f "$NAME" >/dev/null 2>&1 || true' EXIT

up=0
for _ in $(seq 1 60); do
  if curl -sf "${AUTH[@]}" -o /dev/null "http://127.0.0.1:${PORT}/api/config"; then up=1; break; fi
  sleep 0.5
done
if [ "$up" = 1 ]; then pass "server answers /api/config"; else
  fail "server did not answer within 30 s"; "$CONTAINER" logs "$NAME" 2>&1 | tail -20
fi

probe() {  # probe PATH  -- 200 or a failure
  local code; code=$(curl -s "${AUTH[@]}" -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}$1")
  if [ "$code" = 200 ]; then pass "$1 → 200"; else fail "$1 → $code"; fi
}
probe /
# Every stylesheet and script the page names, so a renamed or split client
# file is checked without this list having to follow it.
for asset in $(curl -s "${AUTH[@]}" "http://127.0.0.1:${PORT}/" | grep -oE '(src|href)="/static/[^"]+"' | cut -d'"' -f2); do
  probe "$asset"
done
# The pinned browser libraries, which only a built wheel carries.
probe /static/vendor/monaco/loader.js
probe /static/vendor/xterm/xterm.js

echo "== /api/doctor  (what the container says about itself)"
doctor=$(curl -s "${AUTH[@]}" "http://127.0.0.1:${PORT}/api/doctor")
python3 - "$doctor" "$GPU" "$TRAIN" <<'EOF' || failed=$((failed + 1))
import json, sys
d = json.loads(sys.argv[1]); want_gpu = sys.argv[2] == "1"; train = sys.argv[3] == "1"
rows = {f["name"]: f for f in d["findings"]}
for f in d["findings"]:
    mark = "ok " if f["ok"] else ("ERR" if f["required"] else "-- ")
    print(f"  {mark}  {f['name']:<12} {f['detail'][:90]}")
bad = []
if not d["healthy"]:
    bad.append("required finding failed")
for name in ("package", "rl-mcp", "rlmcp cli"):
    if not rows[name]["ok"]:
        bad.append(f"{name} not ok")
if want_gpu and not rows["gpu"]["ok"]:
    bad.append("GPU asked for but not visible inside the container")
if not want_gpu and rows["gpu"]["ok"]:
    print("  note: a GPU is visible without --gpu; the engine passes it through by default")
# A fresh machine has no tasks and no menagerie: that is the honest answer,
# and the page's job is to say so, not to hide it. The train image is the
# exception on tasks: mjlab is installed, so its built-ins must be listed.
if train:
    if not rows["tasks"]["ok"]:
        bad.append("train image lists no tasks: " + rows["tasks"]["detail"][:120])
else:
    for name in ("tasks", "menagerie"):
        if rows[name]["ok"]:
            print(f"  note: {name} reported ok on a supposedly fresh machine")
if bad:
    print("  FAIL " + "; ".join(bad)); sys.exit(1)
print("  ok   doctor: required items present, advisory ones reported with a fix")
EOF

tasks=$(curl -s "${AUTH[@]}" "http://127.0.0.1:${PORT}/api/tasks" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)["tasks"]))')
if [ "$TRAIN" = 1 ]; then
  if [ "$tasks" -gt 0 ]; then pass "$tasks tasks listed from the studio's own environment"; else
    fail "the train image lists no tasks"; fi
elif [ "$tasks" = 0 ]; then pass "no tasks: the page opens on the first-run state"; else
  fail "a fresh container lists $tasks task(s)"; fi

if [ "$TRAIN" = 1 ]; then
  echo "== train  ($TASK through /api/build/launch, a few iterations)"
  # The launch button, without the button: the same request the Build view
  # sends. A record is opened first, then rl-mcp trains in the studio's own
  # interpreter (there is no task repository beside the records here).
  launch=$(curl -s "${AUTH[@]}" -X POST -H 'content-type: application/json' \
    -d "{\"task\":\"$TASK\",\"slug\":\"fresh_smoke\",\"hypothesis\":\"a fresh install trains at all\",\"num_envs\":256,\"max_iterations\":5}" \
    "http://127.0.0.1:${PORT}/api/build/launch")
  job=$(python3 -c 'import sys,json; d=json.loads(sys.argv[1]); print(d.get("job",{}).get("id","") if d.get("ok") else "")' "$launch")
  if [ -z "$job" ]; then
    fail "launch refused: $(printf '%s' "$launch" | head -c 300)"
  else
    pass "launch accepted: job $job, record $(python3 -c 'import sys,json; print(json.loads(sys.argv[1])["record"])' "$launch")"
    state=""
    for _ in $(seq 1 240); do   # up to 20 min: the first import compiles warp kernels
      state=$(curl -s "${AUTH[@]}" "http://127.0.0.1:${PORT}/api/build/launches" | python3 -c '
import sys, json
jobs = json.load(sys.stdin)
jobs = jobs.get("launches", jobs.get("jobs", jobs)) if isinstance(jobs, dict) else jobs
for j in jobs:
    if j.get("id") == sys.argv[1]:
        print(j.get("state", "")); break
else:
    print("gone")' "$job")
      case "$state" in succeeded|failed|cancelled|gone) break ;; esac
      sleep 5
    done
    if [ "$state" = succeeded ]; then pass "the run finished: state=$state"; else
      fail "the run ended as '$state'"
      "$CONTAINER" logs "$NAME" 2>&1 | tail -30
      curl -s "${AUTH[@]}" "http://127.0.0.1:${PORT}/api/build/launches" | python3 -c '
import sys, json
jobs = json.load(sys.stdin); jobs = jobs.get("launches", jobs.get("jobs", jobs)) if isinstance(jobs, dict) else jobs
for j in jobs:
    if j.get("id") == sys.argv[1]:
        print(json.dumps({k: v for k, v in j.items() if k != "log"}, indent=1)[:1500])
        print("\n".join(str(line) for line in (j.get("log") or [])[-25:]))' "$job"
    fi
    runs=$(curl -s "${AUTH[@]}" "http://127.0.0.1:${PORT}/api/runs?task=$TASK" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)["runs"]))')
    if [ "$runs" -gt 0 ]; then pass "$runs record(s) under $TASK in the rail"; else
      fail "no record under $TASK after the launch"; fi
  fi
fi

if [ "$SHELL_AFTER" = 1 ]; then
  echo "== shell  (the server is still up on :${PORT}; exit to tear down)"
  "$CONTAINER" exec -it "$NAME" bash
fi

echo
if [ "$failed" = 0 ]; then echo "fresh install: every probe passed"; else
  echo "fresh install: $failed probe(s) failed"; fi
exit "$failed"
