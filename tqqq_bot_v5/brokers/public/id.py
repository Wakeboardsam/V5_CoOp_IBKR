import uuid

def new_public_order_id() -> str:
    return str(uuid.uuid4())
