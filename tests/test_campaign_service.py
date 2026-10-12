from types import SimpleNamespace

import pytest

from app.services import campaigns


class MemoryPipeline:
    def __init__(self, client):
        self.client = client
        self.commands = []

    def __getattr__(self, name):
        def add(*args, **kwargs):
            self.commands.append((name, args, kwargs))
            return self
        return add

    def execute(self):
        return [getattr(self.client, name)(*args, **kwargs) for name, args, kwargs in self.commands]


class MemoryLock:
    def acquire(self, blocking=True):
        return True

    def release(self):
        return None


class MemoryRedis:
    def __init__(self):
        self.hashes = {}
        self.lists = {}
        self.sets = {}
        self.zsets = {}
        self.strings = {}

    def pipeline(self, transaction=True):
        return MemoryPipeline(self)

    def lock(self, *args, **kwargs):
        return MemoryLock()

    def hset(self, key, field=None, value=None, mapping=None):
        target = self.hashes.setdefault(key, {})
        if mapping is not None:
            target.update({str(k): str(v) for k, v in mapping.items()})
        elif field is not None:
            target[str(field)] = str(value)
        return 1

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def exists(self, key):
        return int(key in self.hashes or key in self.lists or key in self.sets or key in self.strings or key in self.zsets)

    def rpush(self, key, *values):
        self.lists.setdefault(key, []).extend(str(v) for v in values)
        return len(self.lists[key])

    def llen(self, key):
        return len(self.lists.get(key, []))

    def lindex(self, key, index):
        values = self.lists.get(key, [])
        try:
            return values[index]
        except IndexError:
            return None

    def lrem(self, key, count, value):
        values = self.lists.get(key, [])
        removed = 0
        if count >= 0:
            kept = []
            for item in values:
                if item == value and (count == 0 or removed < count):
                    removed += 1
                else:
                    kept.append(item)
            self.lists[key] = kept
        else:
            reversed_values = list(reversed(values))
            kept = []
            for item in reversed_values:
                if item == value and removed < abs(count):
                    removed += 1
                else:
                    kept.append(item)
            self.lists[key] = list(reversed(kept))
        return removed

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update({str(k): float(v) for k, v in mapping.items()})
        return len(mapping)

    def _zitems(self, key, reverse=False):
        return sorted(self.zsets.get(key, {}).items(), key=lambda item: (item[1], item[0]), reverse=reverse)

    def zrange(self, key, start, end):
        ids = [item[0] for item in self._zitems(key)]
        if end < 0:
            end = len(ids) + end
        return ids[start:end + 1]

    def zrevrange(self, key, start, end):
        ids = [item[0] for item in self._zitems(key, reverse=True)]
        if end < 0:
            end = len(ids) + end
        return ids[start:end + 1]

    def sadd(self, key, *values):
        target = self.sets.setdefault(key, set())
        before = len(target)
        target.update(str(v) for v in values)
        return len(target) - before

    def srem(self, key, *values):
        target = self.sets.setdefault(key, set())
        before = len(target)
        target.difference_update(str(v) for v in values)
        return before - len(target)

    def scard(self, key):
        return len(self.sets.get(key, set()))

    def hincrby(self, key, field, amount=1):
        target = self.hashes.setdefault(key, {})
        value = int(target.get(field, "0")) + amount
        target[field] = str(value)
        return value

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.strings:
            return None
        self.strings[key] = str(value)
        return True

    def get(self, key):
        return self.strings.get(key)

    def delete(self, *keys):
        removed = 0
        for key in keys:
            for store in (self.hashes, self.lists, self.sets, self.strings, self.zsets):
                if key in store:
                    del store[key]
                    removed += 1
        return removed

    def expire(self, key, seconds):
        return True

    def eval(self, _script, _num_keys, key, expected):
        if self.strings.get(key) == expected:
            self.strings.pop(key, None)
            return 1
        return 0

    def hmget(self, key, *fields):
        row = self.hashes.get(key, {})
        return [row.get(field) for field in fields]


def _mock_tasks(monkeypatch):
    from app.services import task_history
    from app.workers import tasks

    dispatched = []

    def register(task_id, task_name, ca_number, *, batch_id=None):
        client = campaigns._client
        client.hset(f"caepi:task_history:item:{task_id}", mapping={
            "task_id": task_id, "task_name": task_name, "ca_number": ca_number,
            "batch_id": batch_id or "", "state": "PENDING",
        })

    def apply_async(*, args, task_id):
        dispatched.append((args[0], task_id))
        return SimpleNamespace(id=task_id)

    fake_task = SimpleNamespace(name="app.workers.tasks.refresh_source", apply_async=apply_async)
    monkeypatch.setattr(tasks, "refresh_source", fake_task)
    monkeypatch.setattr(tasks, "fetch_certificate_pdf", fake_task)
    monkeypatch.setattr(task_history, "register_queued", register)
    monkeypatch.setattr(task_history, "update_task", lambda *a, **k: None)
    return dispatched


def test_campaign_dispatches_in_controlled_waves_and_completes(monkeypatch):
    memory = MemoryRedis()
    monkeypatch.setattr(campaigns, "_client", memory)
    dispatched = _mock_tasks(monkeypatch)
    campaign = campaigns.create_campaign(
        ["10001", "10002", "10003", "10004", "10005", "10006"],
        scope="unqueried", scope_label="Sem consulta recente", operation="refresh", stale_days=90,
    )
    campaign_id = campaign["campaign_id"]

    first = campaigns.dispatch_campaign_work(max_dispatch_per_tick=20)
    current = campaigns.get_campaign(campaign_id)
    assert first["dispatched"] == 4
    assert current["active"] == 4
    assert current["remaining"] == 2

    for _ca, task_id in list(dispatched):
        campaigns.record_task_terminal(campaign_id, task_id, "SUCCESS")
    second = campaigns.dispatch_campaign_work(max_dispatch_per_tick=20)
    assert second["dispatched"] == 2
    for _ca, task_id in dispatched[4:]:
        campaigns.record_task_terminal(campaign_id, task_id, "SUCCESS")

    final = campaigns.get_campaign(campaign_id)
    assert final["state"] == "COMPLETED"
    assert final["succeeded"] == 6
    assert final["failed"] == 0
    assert final["remaining"] == 0


def test_only_one_active_campaign_and_pause_resume_cancel(monkeypatch):
    memory = MemoryRedis()
    monkeypatch.setattr(campaigns, "_client", memory)
    first = campaigns.create_campaign(
        ["20001", "20002", "20003"], scope="all", scope_label="Todos", operation="refresh", stale_days=90,
    )
    with pytest.raises(RuntimeError):
        campaigns.create_campaign(
            ["30001"], scope="all", scope_label="Todos", operation="refresh", stale_days=90,
        )

    paused = campaigns.pause_campaign(first["campaign_id"])
    assert paused["state"] == "PAUSED"
    resumed = campaigns.resume_campaign(first["campaign_id"])
    assert resumed["state"] == "RUNNING"
    cancelled = campaigns.cancel_campaign(first["campaign_id"])
    assert cancelled["state"] == "CANCELLED"
    assert cancelled["skipped"] == 3
    assert campaigns.find_active_campaign() is None
