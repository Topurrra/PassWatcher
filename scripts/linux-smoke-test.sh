#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
project_root="$(cd -- "$script_dir/.." && pwd -P)"
bundle="$project_root/src/passwatcher/assets/passwatcher-server.pyz"
smoke_home="$(mktemp -d)"

cleanup() {
    if [[ -n "${smoke_home:-}" && -d "$smoke_home" ]]; then
        rm -rf -- "$smoke_home"
    fi
}
trap cleanup EXIT

export PASSWATCHER_DATA_DIR="$smoke_home/.local/share/passwatcher"
health_request='{"protocol_version":1,"operation":"health","payload":{}}'
create_request='{"protocol_version":1,"operation":"create","payload":{"service":"smoke.example","label":"test","username":"smoke-user","password":"smoke-secret","notes":""}}'

first_health="$(printf '%s\n' "$health_request" | python3 "$bundle" rpc)"
python3 -c 'import json,sys; value=json.loads(sys.argv[1]); assert value["ok"] is True; assert value["result"]["record_count"] == 0' "$first_health"

create_result="$(printf '%s\n' "$create_request" | python3 "$bundle" rpc)"
python3 -c 'import json,sys; value=json.loads(sys.argv[1]); assert value["ok"] is True' "$create_result"

second_health="$(printf '%s\n' "$health_request" | python3 "$bundle" rpc)"
python3 -c 'import json,sys; value=json.loads(sys.argv[1]); assert value["ok"] is True; assert value["result"]["record_count"] == 1; assert value["result"]["integrity_check"] == "ok"' "$second_health"

if [[ "$(python3 -c 'import os; print(os.name)')" == "posix" ]]; then
    python3 -c 'import os,stat,sys; assert stat.S_IMODE(os.stat(sys.argv[1]).st_mode) == 0o600' "$PASSWATCHER_DATA_DIR/passwatcher.db"
fi

printf '%s\n' "Linux smoke test passed: one database reused with one record and secure permissions."
