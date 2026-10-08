from dataclasses import dataclass
import json
from pathlib import Path

STAGES = ("amr", "packer", "agv")

@dataclass
class MaterialRequest:
    request_id: str
    arrival_min: float
    origin_id: str
    destination_line_id: str
    material_id: str
    quantity: int

    def __post_init__(self):
        if self.quantity <= 0:
            raise ValueError("Quantity must be greater than 0")

        if self.arrival_min < 0:
            raise ValueError("Arrival Min must be greater than 0")

        if self.request_id == "":
            raise ValueError("Required Request ID")

@dataclass
class ResourceStatus:
    total: int
    busy: int
    offline: int

    def __post_init__(self):
        if self.total < 0:
            raise ValueError("Total must be greater than 0")

        if self.busy < 0:
            raise ValueError("Busy must be >= 0")

        if self.offline < 0:
            raise ValueError("Offline must be greater than 0")

        if self.busy + self.offline > self.total:
            raise ValueError("Busy + Offline have to be smaller than total")

@dataclass
class QueuedJob:
    stage: str
    request: MaterialRequest

    def __post_init__(self):
        if self.stage not in ("amr", "agv", "packer"):
            raise ValueError("Stage have to be AMR, AGV or packer")
        
@dataclass
class ActiveJob:
    stage: str
    remaining_min: float
    request: MaterialRequest
    
    def __post_init__(self):
        if self.stage not in ("amr", "agv", "packer"):
            raise ValueError("Stage have to be AMR, AGV or packer")    
        if self.remaining_min <= 0:
            raise ValueError("Remaining min can't be negative")

@dataclass
class CurrentLogisticsState:  
    as_of: str
    resources: dict[str, ResourceStatus]
    queued_jobs: list[QueuedJob]   
    active_jobs: list[ActiveJob]   
    feed_updated_at: dict[str, str]

    def __post_init__(self):
        for stage, status in self.resources.items():
            active_count = sum(job.stage == stage for job in self.active_jobs)        
            if status.busy != active_count:
                raise ValueError(
                f"{stage}: busy={status.busy}, nhưng có "
                f"{active_count} active jobs"
            )

@dataclass
class ScenarioConfig:
    scenario_id: str
    horizon_min: float
    sample_every_min:float
    random_seed: int
    capacity: dict[str, int]
    service_min: dict[str, float]

    def __post_init__(self) -> None:
        if set(self.capacity) != set(STAGES):
            raise ValueError("capacity must have amr, packer, agv")
        if set(self.service_min) != set(STAGES):
            raise ValueError("service_min must have amr, packer, agv")
        if self.horizon_min <= 0 or self.sample_every_min <= 0:
            raise ValueError("Time must be positve")
        if any(x <= 0 for x in self.capacity.values()):
            raise ValueError("Ccapacity must be positve")
        if any(x <= 0 for x in self.service_min.values()):
            raise ValueError("service_min must be positve")    

@dataclass
class SimulationResult:
    scenario_id: str
    horizon_min: float
    initial_state_used: bool
    input_count: int
    metrics: dict[str, object]
    timeline: list[dict]
    events: list[dict]
if __name__ == "__main__":
    raw = json.loads(
        Path("contracts/mock_v0_1.json").read_text(encoding="utf-8")
    )
    amr_data = raw["current_state"]["resources"]["amr"]

    jobs = []
    for item in raw["current_state"]["queued_jobs"]:
        req = MaterialRequest(**item['request'])
        job = QueuedJob(stage=item['stage'], request=req)
        jobs.append(job)
    # print(jobs)
    Activejobs = []
    for item in raw["current_state"]["active_jobs"]:
        req = MaterialRequest(**item['request'])
        job = ActiveJob(stage=item['stage'], remaining_min=item['remaining_min'], request=req)
        Activejobs.append(job)
    # print(Activejobs)    

    amr = ResourceStatus(**raw['current_state']['resources']['amr'])
    agv = ResourceStatus(**raw['current_state']['resources']['agv'])
    packer = ResourceStatus(**raw['current_state']['resources']['packer'])
    resources = {
        "amr": amr,
        "agv": agv,
        "packer": packer,
    }
    state  = CurrentLogisticsState(
        as_of=raw['current_state']['as_of'],
        resources=resources,
        queued_jobs=jobs,
        active_jobs=Activejobs,
        feed_updated_at=raw['current_state']["feed_updated_at"]
    )

    # print(state.as_of)
    # print(state.resources["amr"].busy)
    # print(len(state.queued_Jobs))
    # print(state.queued_Jobs[0].request.request_id)
    capacity_test = {
        "amr": 5,
        "agv": 4,
        "packer": 3,        
    }
    servMin_test = {
        "amr": 4.0,
        "agv": 3.0,
        "packer": 5.5,        
    }    
    scenarioconf =ScenarioConfig(scenario_id="BASELINE", horizon_min=60, sample_every_min=5, 
                                 random_seed=42, capacity=capacity_test,
                                 service_min=servMin_test)

    print(scenarioconf)