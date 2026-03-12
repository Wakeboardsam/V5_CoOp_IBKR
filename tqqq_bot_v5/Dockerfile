FROM python:3.11-slim-bookworm

# Install system dependencies
RUN apt-get update && apt-get install -y \
    default-jre \
    supervisor \
    wget \
    unzip \
    jq \
    procps \
    libxtst6 \
    libxrender1 \
    libxi6 \
    gettext-base \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install IB Gateway
# We use the x64 installer. For aarch64, it's expected to run via emulation in the target environment if native is unavailable.
RUN wget -q https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh \
    && chmod +x ibgateway-stable-standalone-linux-x64.sh \
    && ./ibgateway-stable-standalone-linux-x64.sh -q -dir /root/Jts/ibgateway \
    && rm ibgateway-stable-standalone-linux-x64.sh
RUN VERSION_DIR=$(dirname $(find /root/Jts -name jars -type d | head -n 1)) && ln -sfn $VERSION_DIR /root/Jts/ibgateway/9999

# Configure IB Gateway memory limit
RUN find /root/Jts/ibgateway -name "*.vmoptions" -exec sh -c 'echo "-Xmx512m" >> "{}"' \;

# Install IBC
RUN wget -q https://github.com/IbcAlpha/IBC/releases/download/3.23.0/IBCLinux-3.23.0.zip \
    && mkdir -p /opt/ibc \
    && unzip IBCLinux-3.23.0.zip -d /opt/ibc \
    && chmod +x /opt/ibc/scripts/*.sh /opt/ibc/*.sh 2>/dev/null || true \
    && rm IBCLinux-3.23.0.zip

# Configure IBC
# Note: config is rendered at runtime by run.sh

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Set entrypoint
RUN chmod +x run.sh
ENTRYPOINT ["/app/run.sh"]
