#!/bin/bash
# Container-side compile-level oracle for one HarmonyOS module.
# Runs `hvigorw test` with ArkTS strict typeCheck and watches the build output for
# the UnitTestArkTS compile marker, resolving the verdict the instant it is known so
# GREEN cases return immediately once the module has compiled.
#
# Env in:  HB_TC (toolchain root, has jdk17 + command-line-tools)
#          HB_REPO (repo root)  HB_MODULE (module name, e.g. common)
#          HB_TIMEOUT (secs, default 240)  HB_PROXY (http proxy url, optional)
# Exit:    0 = GREEN (UnitTestArkTS compiled)   1 = RED (compile/type error)
#          2 = ERROR (neither marker before timeout / setup failure)
set -u
: "${HB_TIMEOUT:=240}"
MOD="$HB_MODULE"
CLT="$HB_TC/command-line-tools"
export JAVA_HOME="$HB_TC/jdk17"
export DEVECO_SDK_HOME="$CLT/sdk"
export NODE_HOME="$CLT/tool/node"
export PATH="$JAVA_HOME/bin:$CLT/bin:$NODE_HOME/bin:$PATH"
if [ -n "${HB_PROXY:-}" ]; then
  export http_proxy="$HB_PROXY" https_proxy="$HB_PROXY"
  export no_proxy=127.0.0.1,localhost NO_PROXY=127.0.0.1,localhost
fi

cd "$HB_REPO" || { echo "HB_ERR: repo not found"; exit 2; }

# defeat incremental cache (stale cache -> false GREEN after a deletion)
MODULE_DIR=""
for candidate in "$MOD" "features/$MOD" "products/$MOD"; do
  if [ -f "$candidate/oh-package.json5" ]; then MODULE_DIR="$candidate"; break; fi
done
if [ -z "$MODULE_DIR" ]; then echo "HB_ERR: module not found: $MOD"; exit 2; fi
rm -rf "$MODULE_DIR/build" "$MODULE_DIR/.test" "$MODULE_DIR/.hvigor" .hvigor/cache 2>/dev/null

LOG="$(mktemp /tmp/hb_hvigor.XXXXXX.log)"
FIN="Finished :$MOD:default@UnitTestArkTS"
FAIL="Failed :$MOD:default@UnitTestArkTS"

GROUP=0
if command -v setsid >/dev/null; then
  setsid hvigorw test -p module="$MOD" --no-daemon >"$LOG" 2>&1 &
  GROUP=1
else
  hvigorw test -p module="$MOD" --no-daemon >"$LOG" 2>&1 &
fi
HPID=$!
cleanup() {
  if [ "$GROUP" = 1 ]; then kill -- "-$HPID" 2>/dev/null; else kill "$HPID" 2>/dev/null; fi
  wait "$HPID" 2>/dev/null
  rm -f "$LOG"
}
trap cleanup EXIT
trap 'exit 2' INT TERM

verdict=2
elapsed=0
while kill -0 "$HPID" 2>/dev/null; do
  if grep -qaF "$FIN" "$LOG"; then verdict=0; break; fi
  if grep -qaF "$FAIL" "$LOG" || grep -qaF "BUILD FAILED" "$LOG"; then verdict=1; break; fi
  sleep 2; elapsed=$((elapsed+2))
  if [ "$elapsed" -ge "$HB_TIMEOUT" ]; then break; fi
done

# if the process already exited on its own, read the final markers
if [ "$verdict" = 2 ]; then
  if grep -qaF "$FIN" "$LOG"; then verdict=0
  elif grep -qaF "$FAIL" "$LOG" || grep -qaF "BUILD FAILED" "$LOG"; then verdict=1; fi
fi

echo "HB_VERDICT=$verdict"
echo "HB_LOG_TAIL_BEGIN"
grep -aE "Finished :$MOD:default@|Failed :$MOD:default@|ArkTS Compiler Error|Error Message:|BUILD (FAILED|SUCCESSFUL)" "$LOG" | tail -25
echo "HB_LOG_TAIL_END"
exit "$verdict"
