from dataclasses import dataclass
import json
from pathlib import Path
from math import isfinite
from decimal import Decimal
from datetime import datetime

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
        if type(self.quantity) is not int:
            raise ValueError("quantity phải là số nguyên")

        if (
            type(self.arrival_min) not in (int, float)
            or not isfinite(self.arrival_min)
        ):
            raise ValueError("arrival_min phải là số hữu hạn")

        for name in (
            "request_id",
            "origin_id",
            "destination_line_id",
            "material_id",
        ):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"{name} phải là chuỗi không rỗng")        
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
        if any(
            type(value) is not int
            for value in (self.total, self.busy, self.offline)
        ):
            raise ValueError("total, busy, offline phải là số nguyên")
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
        if not isinstance(self.request, MaterialRequest):
            raise ValueError("request phải là MaterialRequest")  
              
        if self.stage not in ("amr", "agv", "packer"):
            raise ValueError("Stage have to be AMR, AGV or packer")
        
@dataclass
class ActiveJob:
    stage: str
    remaining_min: float
    request: MaterialRequest
    
    def __post_init__(self):
        if not isinstance(self.request, MaterialRequest):
            raise ValueError("request phải là MaterialRequest")  
                      
        if self.stage not in ("amr", "agv", "packer"):
            raise ValueError("Stage have to be AMR, AGV or packer")    
        if (
            type(self.remaining_min) not in (int, float)
            or not isfinite(self.remaining_min)
        ):
            raise ValueError("remaining_min phải là số hữu hạn")    
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
        if (
            not isinstance(self.resources, dict)
            or set(self.resources) != set(STAGES)
        ):
            raise ValueError("resources phải có đúng amr, packer, agv")

        for stage, status in self.resources.items():
            if not isinstance(status, ResourceStatus):
                raise ValueError(f"resources.{stage} phải là ResourceStatus")

        if (
            not isinstance(self.active_jobs, list)
            or any(not isinstance(job, ActiveJob) for job in self.active_jobs)
        ):
            raise ValueError("active_jobs phải là list chứa ActiveJob")

        if (
            not isinstance(self.queued_jobs, list)
            or any(not isinstance(job, QueuedJob) for job in self.queued_jobs)
        ):
            raise ValueError("queued_jobs phải là list chứa QueuedJob")

        seen_ids = set()

        for job in (*self.active_jobs, *self.queued_jobs):
            if job.request.arrival_min != 0:
                raise ValueError("Request WIP phải có arrival_min = 0")

            request_id = job.request.request_id

            if request_id in seen_ids:
                raise ValueError(f"Trùng request_id trong WIP: {request_id}")

            seen_ids.add(request_id)   

        for stage, status in self.resources.items():
            active_count = sum(job.stage == stage for job in self.active_jobs)        
            if status.busy != active_count:
                raise ValueError(
                f"{stage}: busy={status.busy}, nhưng có "
                f"{active_count} active jobs"
            )
        if not isinstance(self.as_of, str):
            raise ValueError("as_of phải là chuỗi ISO 8601")

        try:
            as_of = datetime.fromisoformat(self.as_of)
        except ValueError as exc:
            raise ValueError("as_of không phải ISO 8601 hợp lệ") from exc

        if as_of.utcoffset() is None:
            raise ValueError("as_of phải có múi giờ")

        if (
            not isinstance(self.feed_updated_at, dict)
            or set(self.feed_updated_at)
            != {"wms", "amr", "agv", "production", "labor", "excel"}
        ):
            raise ValueError("feed_updated_at phải có đúng 6 nguồn feed")

        for name, value in self.feed_updated_at.items():
            if not isinstance(value, str):
                raise ValueError(f"feed_updated_at.{name} phải là chuỗi ISO 8601")

            try:
                updated_at = datetime.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(
                    f"feed_updated_at.{name} không phải ISO 8601 hợp lệ"
                ) from exc

            if updated_at.utcoffset() is None:
                raise ValueError(f"feed_updated_at.{name} phải có múi giờ")

            if updated_at > as_of:
                raise ValueError(f"feed_updated_at.{name} vượt quá as_of")
            
@dataclass
class ScenarioConfig:
    scenario_id: str
    horizon_min: float
    sample_every_min:float
    random_seed: int
    capacity: dict[str, int]
    service_min: dict[str, float]

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_id, str) or not self.scenario_id:
            raise ValueError("scenario_id phải là chuỗi không rỗng")

        if type(self.random_seed) is not int:
            raise ValueError("random_seed phải là số nguyên")

        if not isinstance(self.capacity, dict):
            raise ValueError("capacity phải là dict")

        if not isinstance(self.service_min, dict):
            raise ValueError("service_min phải là dict")

        if any(type(x) is not int for x in self.capacity.values()):
            raise ValueError("capacity phải chứa số nguyên")

        for x in (
            self.horizon_min,
            self.sample_every_min,
            *self.service_min.values(),
        ):
            if type(x) not in (int, float) or not isfinite(x):
                raise ValueError("Các giá trị thời gian phải là số hữu hạn")        
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
        if (
            Decimal(str(self.horizon_min))
            % Decimal(str(self.sample_every_min))
            != 0
        ):
            raise ValueError("sample_every_min phải chia hết horizon_min")        

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