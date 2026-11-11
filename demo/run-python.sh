#!/bin/sh
pip install -q httpx || exit 1
exec python "$@"
