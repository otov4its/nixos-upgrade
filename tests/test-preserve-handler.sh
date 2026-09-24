#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PROJECT_ROOT

python3 - "$PROJECT_ROOT/src/lib" <<'PY'
import collections.abc

import signal
import sys
import typing

sys.path.insert(0, sys.argv[1])
import synsignals

assert typing.get_origin(synsignals.PreserveHandler) is collections.abc.Callable
assert synsignals.preserve_always(signal.SIGTERM)
assert not synsignals.preserve_never(signal.SIGTERM)
assert callable(synsignals.preserve_if_not_dfl)
print("ok: PreserveHandler is a Callable policy type")
PY
