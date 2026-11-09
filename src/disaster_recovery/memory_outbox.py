"""Thread-safe storage MODEL for fault injection, not production durability."""
import copy
import threading
from .outbox import LostLease


class MemoryOutbox:
    def __init__(self, records=()):
        self.items = {r['order_id']:copy.deepcopy(r) for r in records}
        self.lock = threading.RLock()
        self.available = True

    def _check(self):
        if not self.available:
            raise ConnectionError('Simulated durable-store failure')

    def put_if_absent(self, record):
        with self.lock:
            self._check()
            return copy.deepcopy(self.items.setdefault(record['order_id'], copy.deepcopy(record)))

    def claim(self, key, token, epoch, now, until):
        with self.lock:
            self._check()
            r = self.items.get(key)
            if not r or r['state'] != 'PENDING' or r.get('due_at',now+1)>now or r.get('epoch',0)>epoch:
                return None
            r.update(lease_token=token, epoch=epoch, due_at=until, attempts=r['attempts']+1)
            return copy.deepcopy(r)

    def sent(self, key, token, epoch, now):
        with self.lock:
            self._check()
            r = self.items[key]
            if r.get('lease_token') != token or r.get('epoch') != epoch or r['state']!='PENDING' or r['due_at']<=now:
                raise LostLease('Stale owner')
            r.update(state='SENT', published_at=now)
            for name in ['lease_token','dispatch_bucket','due_at']:r.pop(name,None)

    def due(self, now, limit=100):
        with self.lock:
            self._check()
            return [r['order_id'] for r in self.items.values() if r['state']=='PENDING' and r['due_at']<=now][:limit]

    def records(self):
        with self.lock:
            self._check()
            return copy.deepcopy(list(self.items.values()))
