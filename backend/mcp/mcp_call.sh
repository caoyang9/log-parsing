#!/bin/bash
# 用法: ./mcp_call.sh <wrapper> <工具名> <key=value> ...
# 示例: ./mcp_call.sh mcp_wrapper.sh quick_stats path=/tmp/log.txt

WRAPPER="$1"
TOOL="$2"
shift 2

# 拼 JSON arguments
ARGS="{"
FIRST=1
for kv in "$@"; do
    k="${kv%%=*}"
    v="${kv#*=}"
    # 判断 v 是不是数字
    if [[ "$v" =~ ^[0-9]+$ ]]; then
        v_json="$v"
    else
        v_json="\"$v\""
    fi
    [[ $FIRST -eq 1 ]] || ARGS+=", "
    ARGS+="\"$k\": $v_json"
    FIRST=0
done
ARGS+="}"

printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{}}}\n{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"%s","arguments":%s}}\n' "$TOOL" "$ARGS" \
    | "$WRAPPER" 2>/dev/null \
    | python3 -c "
import sys, json
for line in sys.stdin:
    r = json.loads(line.strip())
    if 'result' in r and 'content' in r['result']:
        print(r['result']['content'][0]['text'])
"
