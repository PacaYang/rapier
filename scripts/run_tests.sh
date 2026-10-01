#!/usr/bin/env bash
#
# Run the Rapier test suite.
#
# Usage:
#   scripts/run_tests.sh [unit|integration|e2e|all] [--cov]
#
# Examples:
#   scripts/run_tests.sh              # run all tests
#   scripts/run_tests.sh unit         # only unit tests
#   scripts/run_tests.sh all --cov    # all tests with coverage report

set -euo pipefail

# Move to repo root (directory containing this script's parent)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

LEVEL="${1:-all}"
COV_FLAG="${2:-}"

PYTEST_ARGS="-v"

# Add coverage if requested
if [[ "${COV_FLAG}" == "--cov" ]]; then
    PYTEST_ARGS="${PYTEST_ARGS} --cov=rapier --cov-report=term-missing --cov-report=html"
fi

case "${LEVEL}" in
    unit)
        echo "Running unit tests..."
        python3 -m pytest tests/unit/ ${PYTEST_ARGS}
        ;;
    integration)
        echo "Running integration tests..."
        python3 -m pytest tests/integration/ ${PYTEST_ARGS}
        ;;
    e2e)
        echo "Running end-to-end tests..."
        python3 -m pytest tests/e2e/ ${PYTEST_ARGS}
        ;;
    methods)
        echo "Running method tests..."
        python3 -m pytest tests/methods/ ${PYTEST_ARGS}
        ;;
    all)
        echo "Running full test suite..."
        python3 -m pytest tests/ ${PYTEST_ARGS}
        ;;
    *)
        echo "Unknown test level: ${LEVEL}"
        echo "Usage: $0 [unit|integration|e2e|methods|all] [--cov]"
        exit 1
        ;;
esac
