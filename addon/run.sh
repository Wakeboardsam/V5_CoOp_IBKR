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
export TWS_PATH=/opt/ibgateway
export IBC_PATH=/opt/ibc
export TWS_SETTINGS_PATH=/opt/ibgateway

# Wait for Gateway port before starting supervisord?
# No, supervisord starts both. But we can use wait_for_gateway.py in botpy's command if needed.
# However, the requirement said run.sh should render and then exec supervisord.
# wait_for_gateway.py is called by run.sh? No, botpy has 30s delay.
# Wait, the prompt says: "Create gateway/wait_for_gateway.py ... This is called by run.sh before starting the bot."
# If I exec supervisord, it never returns. I should probably use it in botpy command or before exec supervisord if I don't use supervisord for gateway?
# No, supervisord MUST run both.

# Let's check the requirement again: "This is called by run.sh before starting the bot."
# If supervisord manages both, I can't call it in run.sh before supervisord because gateway isn't running yet.
# I'll modify the botpy command in supervisord.conf to call wait_for_gateway.py first.

exec /usr/bin/supervisord -c /app/addon/supervisord.conf
