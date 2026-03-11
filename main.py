import sys
from config.loader import load_config
from brokers.ibkr.adapter import IBKRAdapter
from brokers.schwab.adapter import SchwabAdapter


def main():
    config = load_config()

    if config.active_broker == "ibkr":
        broker = IBKRAdapter(
            host=config.ibkr_host,
            port=config.ibkr_port,
            client_id=config.ibkr_client_id,
            paper=config.paper_trading
        )
    elif config.active_broker == "schwab":
        broker = SchwabAdapter()
    else:
        print(f"Error: Unsupported broker '{config.active_broker}'", file=sys.stderr)
        sys.exit(1)

    mode = "paper" if config.paper_trading else "live"
    print(f"Bot initialized with {config.active_broker} in {mode} mode")


if __name__ == "__main__":
    main()
