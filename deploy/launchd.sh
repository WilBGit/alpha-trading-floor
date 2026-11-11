#!/bin/sh
set -eu
ROOT="/path/to/project/ssd/alpha-trading-floor"
PLIST="$ROOT/deploy/com.alphatradingfloor.engine.plist"
TARGET="$HOME/Library/LaunchAgents/com.alphatradingfloor.engine.plist"
mkdir -p "$ROOT/logs" "$HOME/Library/LaunchAgents"
case "${1:-status}" in
  install) cp "$PLIST" "$TARGET"; plutil -lint "$TARGET"; echo installed ;;
  start) launchctl load "$TARGET" ;;
  stop) launchctl unload "$TARGET" ;;
  restart) launchctl unload "$TARGET" 2>/dev/null || true; launchctl load "$TARGET" ;;
  status) launchctl print "gui/$(id -u)/com.alphatradingfloor.engine" ;;
  uninstall) launchctl unload "$TARGET" 2>/dev/null || true; rm -f "$TARGET" ;;
  *) echo "usage: $0 {install|start|stop|restart|status|uninstall}" >&2; exit 2 ;;
esac
