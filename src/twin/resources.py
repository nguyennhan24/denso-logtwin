import simpy
from .entities import ScenarioConfig


def make_resources(env, config):
    pools ={}

    for stage, capacity in config.capacity.items():
       res = simpy.Resource(env, capacity=capacity) 
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
