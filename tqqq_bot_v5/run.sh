#!/bin/bash
echo "Parsing Home Assistant options..."
IBKR_USER=$(jq -r '.ibkr_username // empty' /data/options.json)
IBKR_PASS=$(jq -r '.ibkr_password // empty' /data/options.json)
export IBKR_PORT=$(jq -r '.ibkr_port // 7497' /data/options.json)
PAPER_FLAG=$(jq -r '.paper_trading' /data/options.json)
TRADING_MODE="paper"
if [ "$PAPER_FLAG" = "false" ]; then
TRADING_MODE="live"
fi
echo "Generating IBC config..."
mkdir -p /root/ibc
cat <<EOF > /root/ibc/config.ini
IbLoginId=${IBKR_USER}
IbPassword=${IBKR_PASS}
TradingMode=${TRADING_MODE}
IbDir=/root/Jts
ReadOnlyApi=no
OverrideTwsApiPort=${IBKR_PORT}
AcceptIncomingConnectionAction=accept
AcceptNonBrokerageAccountWarning=yes
BypassOrderPrecautions=yes
AllowBlindTrading=yes
EOF
echo "Injecting API bypass settings directly into jts.ini..."
mkdir -p /root/Jts
touch /root/Jts/jts.ini
grep -q "BypassOrderPrecautions" /root/Jts/jts.ini || echo "BypassOrderPrecautions=true" >> /root/Jts/jts.ini
grep -q "BypassRedirectOrderWarning" /root/Jts/jts.ini || echo "BypassRedirectOrderWarning=true" >> /root/Jts/jts.ini

# Encrypted settings injection
SETTINGS_FILE="/app/tws.20260405.222111.ibgzenc"
PROFILE_ID="bfmeflenhmfhdgmnkkccpggjcdgjdkanjkgfdgca"
TWS_MAJOR_VRSN=1019

inject_settings() {
    local target_dir=$1
    local target_file="$target_dir/$(basename "$SETTINGS_FILE")"

    mkdir -p "$target_dir"

    if [ -f "$target_file" ]; then
        echo "Backing up existing settings file at $target_file"
        mv "$target_file" "${target_file}.bak_$(date +%Y%m%d%H%M%S)"
    fi

    echo "Copying encrypted settings to $target_dir"
    cp "$SETTINGS_FILE" "$target_file"
    chmod 644 "$target_file"
}

if [ -f "$SETTINGS_FILE" ]; then
    echo "Preparing encrypted IBKR settings injection..."
    echo "Found encrypted settings file: $SETTINGS_FILE"
    echo "WARNING: Source settings are from Gateway 1044, but container is using $TWS_MAJOR_VRSN."

    # Inject into candidate locations
    inject_settings "/root/Jts"
    inject_settings "/root/Jts/$TWS_MAJOR_VRSN"
    inject_settings "/root/Jts/$TWS_MAJOR_VRSN/$PROFILE_ID"
else
    echo "Encrypted settings file NOT found at $SETTINGS_FILE. Skipping injection."
fi

echo "Starting Xvfb..."
Xvfb :99 -ac -screen 0 1024x768x16 &
export DISPLAY=:99
echo "Starting IB Gateway via IBC..."
export TWS_MAJOR_VRSN=1019
export TWS_PATH=/root/Jts
export IBC_PATH=/opt/ibc
/opt/ibc/gatewaystart.sh -inline < /dev/null &
echo "Waiting 30 seconds for Gateway to initialize..."
sleep 30

# Post-startup detection for new profiles
if [ -f "$SETTINGS_FILE" ]; then
    echo "Scanning for newly created profile directories..."
    for d in /root/Jts/$TWS_MAJOR_VRSN/*/; do
        [ -d "$d" ] || continue
        dir_name=$(basename "$d")
        # Profile directories are typically long alphanumeric strings
        if [ ${#dir_name} -gt 20 ]; then
            if [ ! -f "$d/$(basename "$SETTINGS_FILE")" ]; then
                echo "Detected new/missing profile directory: $dir_name. Injecting settings..."
                inject_settings "$d"
                echo "Injection complete for $dir_name. A restart may be required if this was not the target profile."
            fi
        fi
    done
fi

echo "=== IBC DIAGNOSTIC LOGS ==="
cat /root/ibc/logs/*.txt 2>/dev/null || echo "No IBC logs found."
echo "==========================="
echo "Starting Supervisord to launch Python Bot..."
exec supervisord -c /etc/supervisor/conf.d/supervisord.conf
