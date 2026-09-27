#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PROJECT_ROOT

TEST_GIT_FLAKE_ONLY=1 bash "$PROJECT_ROOT/tests/test-user-workflow.sh"
