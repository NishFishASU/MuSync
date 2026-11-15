#!/usr/bin/env bash
# Compatibility wrapper — prefer ./scripts/pair_hci.sh (generic BlueZ HCI).
# Kept so older docs / muscle memory keep working.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$ROOT/pair_hci.sh" "$@"
