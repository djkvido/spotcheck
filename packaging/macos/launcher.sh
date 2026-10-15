#!/bin/sh
RESOURCES="$(cd "$(dirname "$0")/../Resources" && pwd)"
exec "$RESOURCES/runtime/bin/python3" -m spotcheck --packaged
