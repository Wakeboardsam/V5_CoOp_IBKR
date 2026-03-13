#!/bin/bash
echo "Parsing Home Assistant options..."
IBKR_USER=$(jq -r '.ibkr_username // empty' /data/options.json)
IBKR_PASS=$(jq -r '.ibkr_password // empty' /data/options.json)
PAPER_FLAG=$(jq -r '.paper_trading' /data/options.json)
TRADING_MODE="paper"
if [ "$PAPER_FLAG" = "false" ]; then
TRADING_MODE="live"
fi
echo "Generating IBC config..."
cat <<EOF > /tmp/ibc_config.ini
IbLoginId=${IBKR_USER}
IbPassword=${IBKR_PASS}
TradingMode=${TRADING_MODE}
IbDir=/root/Jts
EOF
echo "Starting Xvfb..."
Xvfb :99 -ac -screen 0 1024x768x16 &
export DISPLAY=:99
echo "Starting IB Gateway via IBC..."
/opt/ibc/gatewaystart.sh 9999 -inline --tws-path=/root/Jts --tws-settings-path=/root/Jts --ibc-ini=/tmp/ibc_config.ini < /dev/null &
echo "Waiting 30 seconds for Gateway to initialize..."
sleep 30
echo "=== SEARCHING FOR AND DUMPING IBC DIAGNOSTIC LOGS ==="
cat /root/ibc/logs/*.txt 2>&1 || echo "No logs found at /root/ibc/logs/"
echo "--- Searching alternate paths ---"
find /root/ibc /opt/ibc /root/Jts -name "*.txt" -print -exec cat {} \; 2>/dev/null
echo "====================================================="
echo "Starting Supervisord to launch Python Bot..."
exec supervisord -c /etc/supervisor/conf.d/supervisord.conf
