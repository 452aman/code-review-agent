"""In-process pub/sub so SSE clients can follow a running review (and replay what they missed)."""
import asyncio
from collections import defaultdict
from datetime import datetime, timezone


class EventHub:
    def __init__(self) -> None:
        self.history: dict[str, list[dict]] = defaultdict(list)
        self.subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)

    async def publish(self, review_id: str, node: str, status: str, data: dict) -> None:
        ev = {"node": node, "status": status, "data": data, "t": datetime.now(timezone.utc).isoformat()}
        self.history[review_id].append(ev)
        for q in list(self.subscribers[review_id]):
            q.put_nowait(ev)

    def subscribe(self, review_id: str) -> tuple[list[dict], asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers[review_id].add(q)
        return list(self.history[review_id]), q

    def unsubscribe(self, review_id: str, q: asyncio.Queue) -> None:
        self.subscribers[review_id].discard(q)

    async def close(self, review_id: str) -> None:
        for q in list(self.subscribers[review_id]):
            q.put_nowait(None)  # sentinel: stream finished

    def forget(self, review_id: str) -> list[dict]:
        self.subscribers.pop(review_id, None)
        return self.history.pop(review_id, [])


hub = EventHub()
