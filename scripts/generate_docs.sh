#!/usr/bin/env bash
#
# Generate / check Rapier documentation.
#
# The documentation in docs/ is authored as standalone Markdown that renders
# directly on GitHub and most Markdown viewers — no build step is required to
# read it.
#
# If Sphinx is installed, this script can also build an HTML site from the
# API docstrings. Sphinx is optional and not a core dependency.
#
# Usage:
#   scripts/generate_docs.sh          # list docs and verify they exist
#   scripts/generate_docs.sh --html   # build HTML with Sphinx (if available)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

DOCS=(
    "docs/installation.md"
    "docs/getting_started.md"
    "docs/pipeline_configuration.md"
    "docs/method_reference.md"
    "docs/method_development_guide.md"
    "docs/architecture.md"
    "docs/troubleshooting.md"
    "docs/api_reference.md"
    "docs/faq.md"
)

echo "Documentation files:"
missing=0
for doc in "${DOCS[@]}"; do
    if [[ -f "${doc}" ]]; then
        echo "  ✓ ${doc}"
    else
        echo "  ✗ ${doc} (MISSING)"
        missing=1
    fi
done

if [[ "${missing}" -ne 0 ]]; then
    echo "Some documentation files are missing."
    exit 1
fi

if [[ "${1:-}" == "--html" ]]; then
    if command -v sphinx-build >/dev/null 2>&1; then
        echo "Building HTML documentation with Sphinx..."
        sphinx-build -b html docs docs/_build/html
        echo "HTML docs written to docs/_build/html"
    else
        echo "Sphinx is not installed; skipping HTML build."
        echo "Install with: pip install sphinx sphinx-rtd-theme"
        exit 1
    fi
fi

echo "Done."
