#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Multi-host concurrent load test for v4.0 ic-engine.
#
# Fans out from STUDIO to N fleet hosts. Each host fires a different
# portfolio_ask prompt against the v4.0 container at TYPHON :18090
# concurrently. The container should serve them all without leaking
# subprocesses or starving any single session. Validates the
# "many agents -> single container" pattern at the wire level.
#
# Usage: bash tools/multi_host_load.sh [container_url]
#
#   container_url defaults to http://192.168.207.61:18090
#
# Output: prints per-host result lines as they complete. Writes JSONL
# to /tmp/multi-host-load-<ts>.jsonl with timing + ic_result presence.
set -euo pipefail

CONTAINER_URL="${1:-http://192.168.207.61:18090}"
TS=$(date +%Y%m%d-%H%M%S)
OUT="/tmp/multi-host-load-$TS.jsonl"

# Fleet hosts that each get one MCP session, firing one different prompt.
# Pick prompts that exercise different envelope sections so we're stressing
# different code paths in ic-engine, not just hammering the same cache key.
declare -a STREAMS=(
  "studio|jasonperlow@127.0.0.1|p01-holdings-1|What is in my portfolio?"
  "pythia|jasonperlow@192.168.207.67|p03-performance-1|How has my portfolio performed this year?"
  "clawpi|ncz@192.168.207.54|p15-bonds-1|Show me my bond exposure and yield-to-maturity for fixed income."
  "argos|jasonperlow@192.168.207.22|p06-news-holdings-1|Any news on my holdings today?"
)

echo "fleet load test: $CONTAINER_URL"
echo "streams: ${#STREAMS[@]}"
echo "output: $OUT"
echo

# Per-stream worker — initializes an MCP session, calls portfolio_ask once.
# Records: host, prompt_id, wallclock ms, http status, ic_result presence,
# narrative size.
fire_stream() {
  local label="$1" target="$2" prompt_id="$3" prompt_text="$4"
  local user="${target%%@*}" host="${target##*@}"
  local t0 t1 wall http_code body sid

  t0=$(date +%s%N)

  # Run init+call sequence on the target host (or locally for "studio")
  if [[ "$host" == "127.0.0.1" ]]; then
    runner=(bash -c)
  else
    runner=(ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new "$target")
  fi

  read -r http_code body < <("${runner[@]}" "
    set -e
    SID=\$(curl -s -m 30 -X POST '$CONTAINER_URL/mcp' \\
      -H 'Accept: application/json, text/event-stream' \\
      -H 'Content-Type: application/json' \\
      -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-06-18\",\"capabilities\":{},\"clientInfo\":{\"name\":\"$label\",\"version\":\"0.1\"}}}' \\
      -D /tmp/h-$$ 2>/dev/null >/dev/null
      grep -i mcp-session-id /tmp/h-$$ | tr -d '\r\n' | awk '{print \$2}'
    )
    curl -s -m 5 -X POST '$CONTAINER_URL/mcp' \\
      -H 'Accept: application/json, text/event-stream' \\
      -H 'Content-Type: application/json' \\
      -H \"mcp-session-id: \$SID\" \\
      -d '{\"jsonrpc\":\"2.0\",\"method\":\"notifications/initialized\"}' >/dev/null
    resp=\$(curl -s -m 240 -X POST '$CONTAINER_URL/mcp' \\
      -H 'Accept: application/json, text/event-stream' \\
      -H 'Content-Type: application/json' \\
      -H \"mcp-session-id: \$SID\" \\
      -d '{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"portfolio_ask\",\"arguments\":{\"question\":\"$prompt_text\"}}}' \\
      -w '\\nHTTPCODE:%{http_code}\\n')
    code=\$(echo \"\$resp\" | grep -oE 'HTTPCODE:[0-9]+' | tail -1 | cut -d: -f2)
    body_len=\$(echo \"\$resp\" | wc -c)
    has_ic=\$(echo \"\$resp\" | grep -oE '\"exit_code\":0' | head -1 | wc -l)
    echo \"\$code \$body_len:\$has_ic\"
    rm -f /tmp/h-$$
  ")
  t1=$(date +%s%N)
  wall=$(( (t1 - t0) / 1000000 ))

  local body_len="${body%%:*}" has_ic="${body##*:}"
  local pass=0
  if [[ "$http_code" == "200" ]] && [[ "$has_ic" == "1" ]]; then pass=1; fi

  echo "[$label] http=$http_code wall=${wall}ms ic=$has_ic body_len=$body_len $([ $pass -eq 1 ] && echo PASS || echo FAIL)"

  printf '{"label":"%s","host":"%s","prompt_id":"%s","wall_ms":%s,"http":"%s","ic_result":%s,"body_len":%s,"pass":%s}\n' \
    "$label" "$host" "$prompt_id" "$wall" "$http_code" "$has_ic" "$body_len" "$pass" >> "$OUT"
}

# Fire all streams in background, all (approximately) at once.
START=$(date +%s)
echo "starting all $((${#STREAMS[@]})) streams at $(date +%H:%M:%S)"
for stream in "${STREAMS[@]}"; do
  IFS='|' read -r label target prompt_id prompt_text <<<"$stream"
  fire_stream "$label" "$target" "$prompt_id" "$prompt_text" &
done
wait
END=$(date +%s)
echo
echo "all streams done in $((END - START))s"

# Summary
total=$(wc -l < "$OUT")
passed=$(grep -c '"pass":1' "$OUT" || true)
echo "summary: $passed / $total streams passed"
