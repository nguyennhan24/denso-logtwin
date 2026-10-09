import simpy
from .resources import make_resources
from .entities import MaterialRequest, ScenarioConfig, SimulationResult
STAGE = ("amr", "packer", "agv")
def material_flow(env, material_request, pools, config, events, start_stage='amr', remaining_min=None, initial_priority=None):
    if start_stage not in STAGE:
        raise ValueError("Stage must be AMR, AGV or Packer")

    def request_slot(stage):
        # Thời điểm đến luôn đứng trước ID: job đã chờ giữ FIFO.
        priority = (env.now, 2, material_request.request_id)
        if stage == start_stage and initial_priority is not None:
            priority = initial_priority

        def on_queued():
            events.append({
                "time_min": env.now,
                "request_id": material_request.request_id,
                "stage": stage,
                "event": "queued",
            })

        return pools[stage].request(priority=priority, on_queued=on_queued)

    if(start_stage == "amr"): # Chỉ chạy nếu start_stage là AMR
        with request_slot("amr") as req:
            yield req
            print(material_request.request_id, 'start', env.now)
            events.append({
                "time_min": env.now,
                "request_id": material_request.request_id,
                "stage": "amr",
                "event": "start",        
            }) 
            if remaining_min is not None:
                duration = remaining_min
            else:
                duration = config.service_min['amr']

            yield env.timeout(duration)

            print(material_request.request_id, 'end', env.now) 
            events.append({
                "time_min": env.now,
                "request_id": material_request.request_id,
                "stage": "amr",
                "event": "finish",        
            }) 

    if(start_stage == 'amr' or start_stage == 'packer'):
        with request_slot("packer") as req:
            yield req
            print(material_request.request_id, 'start', env.now)

            events.append({
                "time_min": env.now,
                "request_id": material_request.request_id,
                "stage": "packer",
                "event": "start",        
            })     
            if start_stage == "packer" and remaining_min is not None:
                duration = remaining_min
            else:
                duration = config.service_min['packer']

            yield env.timeout(duration)

            print(material_request.request_id, 'end', env.now)
            events.append({
                "time_min": env.now,
                "request_id": material_request.request_id,
                "stage": "packer",
                "event": "finish",        
            }) 

    with request_slot("agv") as req:
        yield req
        print(material_request.request_id, 'start', env.now)
                
        events.append({
            "time_min": env.now,
            "request_id": material_request.request_id,
            "stage": "agv",
            "event": "start",        
        })  

        if start_stage == 'agv' and remaining_min is not None:
            duration = remaining_min
        else:
            duration = config.service_min['agv']

        yield env.timeout(duration)

        print(material_request.request_id, 'end', env.now) 
        events.append({
            "time_min": env.now,
            "request_id": material_request.request_id,
            "stage": "agv",
            "event": "finish",        
        })

    print(material_request.request_id, "Delivered to line", env.now)
    events.append({
        "time_min": env.now,
        "request_id": material_request.request_id,
        "stage": "line",
        "event": "delivered",        
    })

if __name__ == "__main__":
    config = ScenarioConfig(
        scenario_id="BASELINE",
        horizon_min=60,
        sample_every_min=5,
        random_seed=42,
        capacity={"amr": 5, "packer": 3, "agv": 4},
        service_min={"amr": 4.0, "packer": 5.5, "agv": 3.0},
    )

    request = MaterialRequest(
        request_id="PICK-00102",
        arrival_min=1.5,
        origin_id="STORE-1",
        destination_line_id="L2",
        material_id="M05",
        quantity=3,
    )    
    events =[]
    env = simpy.Environment()
    pools = make_resources(env, config)
    env.process(material_flow(env, request, pools, config, events, start_stage="amr", remaining_min=2))
    env.run(until=30)
    print(events)
