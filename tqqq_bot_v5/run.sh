#!/bin/bash
set -e

CONFIG_PATH="/data/options.json"

# Extract credentials and mode using jq
export IBKR_USERNAME=$(jq -r '.ibkr_username // empty' $CONFIG_PATH)
export IBKR_PASSWORD=$(jq -r '.ibkr_password // empty' $CONFIG_PATH)
PAPER_TRADING=$(jq -r '.paper_trading' $CONFIG_PATH)

if [ "$PAPER_TRADING" = "true" ]; then
    export TRADING_MODE="paper"
    export IBKR_PORT=7497
else
    export TRADING_MODE="live"
    export IBKR_PORT=7496
fi

# Render IBC config template
cat /app/gateway/ibc_config.ini.template | envsubst > /tmp/ibc_config.ini

# Export variables for IBC
export IBC_INI=/tmp/ibc_config.ini
export TWS_PATH=/root/Jts
export IBC_PATH=/opt/ibc
export TWS_SETTINGS_PATH=/root/Jts

echo "Starting Xvfb..."
Xvfb :99 -ac -screen 0 1024x768x16 &
export DISPLAY=:99

echo "Starting IB Gateway via IBC..."
/opt/ibc/gatewaystart.sh 9999 -inline --tws-path=/root/Jts --tws-settings-path=/root/Jts --ibc-ini=/tmp/ibc_config.ini &

echo "Waiting 30 seconds for Gateway to initialize..."
sleep 30

echo "Starting Supervisord to launch Python Bot..."
exec supervisord -c /etc/supervisor/conf.d/supervisord.conf
