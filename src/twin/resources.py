import simpy
from simpy.events import Event, NORMAL
from .entities import ScenarioConfig


class _DispatchEvent(Event):
    """Cấp slot sau khi các event thường tại cùng phút đã được xử lý."""

    def __init__(self, env, callback):
        super().__init__(env)
        self._ok = True
        self._value = None
        self.callbacks.append(callback)
        env.schedule(self, priority=NORMAL + 1, delay=0)


class StageResource(simpy.PriorityResource):
    """FIFO theo phút đến; các lượt cùng tới được xét theo khóa ưu tiên."""

    def __init__(self, env, capacity):
        self._dispatch_pending = False
        super().__init__(env, capacity)

    def request(self, priority=None, on_queued=None):
        if priority is None:
            priority = (self._env.now, 2, "")
        req = super().request(priority=priority)
        req.on_queued = on_queued
        req.queued_logged = False
        return req

    def _trigger_put(self, get_event):
        # Không cấp slot ngay cho callback đầu tiên tại một timestamp.
        if not self._dispatch_pending:
            self._dispatch_pending = True
            _DispatchEvent(self._env, self._dispatch)

    def _dispatch(self, event):
        self._dispatch_pending = False
        # Hook gốc cấp một slot mỗi lượt; lặp để tận dụng đủ capacity.
        while self.queue and self.count < self.capacity:
            super()._trigger_put(None)

        # Chỉ log queued khi sau khi cấp slot job vẫn phải chờ.
        for req in self.queue:
            if not req.queued_logged:
                req.queued_logged = True
                if req.on_queued is not None:
                    req.on_queued()


def make_resources(env, config):
    pools ={}

    for stage, capacity in config.capacity.items():
       res = StageResource(env, capacity=capacity)
       pools[stage] = res

    return pools

if __name__ == "__main__":
    config = ScenarioConfig(
        scenario_id="BASELINE",
        horizon_min=60,
        sample_every_min=5,
        random_seed=42,
        capacity={"amr": 5, "packer": 3, "agv": 4},
        service_min={"amr": 4.0, "packer": 5.5, "agv": 3.0},
    )   
    env = simpy.Environment()
    res = make_resources(env, config)
    print(res.keys())
    print(res["amr"].capacity, res["packer"].capacity, res["agv"].capacity)
