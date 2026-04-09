import re

def apply_patch(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    search_block = """        # Bug 1 Fix: Write G7 only after a full sell cycle complete
        if self.last_broker_shares > 0 and broker_shares == 0:
            logger.info("Full sell cycle detected (shares went to 0). Updating G7 anchor.")
            await self._write_fresh_anchor_ask()"""

    replace_block = """        # Bug 1 Fix: Write G7 only after a full sell cycle complete
        if self.last_broker_shares > 0 and broker_shares == 0:
            logger.info("Full sell cycle detected (shares went to 0). Updating G7 anchor.")
            await self._write_fresh_anchor_ask()

            # Immediately update last_broker_shares to prevent triggering again
            self.last_broker_shares = broker_shares

            # Anchor reset phase entered
            logger.info("Anchor reset phase entered. Halting further trading evaluations for this tick.")
            return"""

    if search_block in content:
        content = content.replace(search_block, replace_block)
        with open(filepath, 'w') as f:
            f.write(content)
        print("Patch 1 applied.")
    else:
        print("Patch 1 failed. Block not found.")

apply_patch('tqqq_bot_v5/engine/engine.py')
