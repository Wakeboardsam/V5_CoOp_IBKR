import re

def apply_patch(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    search_block = """                        elif row.row_index > distal_y:
                            if mismatch_active:
                                logger.warning(f"Skipping BUY order for row {row.row_index} due to share mismatch")
                                continue
                            if getattr(self, '_is_weekend_gap', False):
                                logger.debug(f"Skipping BUY order for row {row.row_index} due to weekend gap")
                                continue

                            # Expect active BUY order
                            if not self.order_manager.has_open_buy(row.row_index):"""

    replace_block = """                        elif row.row_index > distal_y:
                            if mismatch_active:
                                logger.warning(f"Skipping BUY order for row {row.row_index} due to share mismatch")
                                continue
                            if getattr(self, '_is_weekend_gap', False):
                                logger.debug(f"Skipping BUY order for row {row.row_index} due to weekend gap")
                                continue

                            # Protective reconciliation for row 7 anchor order
                            if row.row_index == 7 and self.order_manager.has_open_buy(7):
                                for o in open_orders:
                                    if o['action'] == 'BUY' and self.order_manager.is_tracked(o['order_id']):
                                        r_index, _ = self.order_manager.get_row_and_action(o['order_id'])
                                        if r_index == 7:
                                            live_qty = o.get('qty')
                                            live_price = o.get('limit_price')
                                            if live_qty != row.shares or live_price != row.buy_price:
                                                logger.warning(f"Anchor order mismatch detected for row 7: live order qty/price={live_qty}@{live_price}, sheet qty/price={row.shares}@{row.buy_price}")
                                                # We skip further processing for this row in this tick (do not auto-cancel-replace yet)
                                                break # Will continue with the outer loop since the outer `if not self.order_manager.has_open_buy` will be false and we do nothing else

                            # Expect active BUY order
                            if not self.order_manager.has_open_buy(row.row_index):"""

    if search_block in content:
        content = content.replace(search_block, replace_block)
        with open(filepath, 'w') as f:
            f.write(content)
        print("Patch 2 applied.")
    else:
        print("Patch 2 failed. Block not found.")

apply_patch('tqqq_bot_v5/engine/engine.py')
