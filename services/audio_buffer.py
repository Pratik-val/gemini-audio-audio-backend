import asyncio
import logging

logger = logging.getLogger(__name__)

class AdaptiveBuffer:
    """High-performance zero-latency audio buffer using asyncio.Queue"""
    def __init__(self, name="Buffer", max_chunks=200):
        self.name = name
        self.queue = asyncio.Queue(maxsize=max_chunks)
        self.active = True

    def add_chunk(self, chunk):
        if not self.active:
            return
        if self.queue.full():
            try:
                self.queue.get_nowait()  # Drop oldest chunk if overflowed to maintain low latency
            except asyncio.QueueEmpty:
                pass
        try:
            self.queue.put_nowait(chunk)
        except asyncio.QueueFull:
            pass

    async def get_chunk(self):
        if not self.active and self.queue.empty():
            return None
        try:
            return await self.queue.get()
        except asyncio.CancelledError:
            return None

    def close(self):
        self.active = False
