# Hoàn thiện core Twin Phase 2

Bản code này đã được áp dụng vào checkout chính ngày 09/10/2026. `src/twin` hiện có cơ chế sửa thứ tự cấp tài nguyên; `tests/test_twin.py` và `examples/phase2_demo.py` đã có trong repo. Đã kiểm tra trực tiếp checkout chính: 52 test PASS, main A/B PASS, demo JSON PASS. Tên class và các field contract giữ nguyên.

## 1. Trạng thái hiện tại

Code đang sử dụng nằm trong `src/twin` tại thư mục gốc `D:\Code\GitHub\denso-logtwin`. Thư mục `output/phase2-code` giữ bản mẫu và patch lịch sử để đối chiếu. Patch đã được áp dụng vào source chính; không cần áp dụng lại. Các phần 2–4 bên dưới giải thích các thay đổi đã có trong source.

Test thực tế là `tests/test_twin.py`. File `output/phase2-code/tests/twin_checks.py` là bản mẫu cùng nội dung, dùng tên khác để tránh pytest thu thập trùng.

## 2. resources.py: gom job cùng timestamp trước khi cấp slot

Chỉ đổi Resource sang PriorityResource chưa đủ: nếu pool rảnh, callback tới trước vẫn được cấp slot ngay. `StageResource` hoãn cấp slot tới sau các event thường tại cùng timestamp, không cộng thêm thời gian mô phỏng. Sau khi tất cả job cùng tới đã đăng ký, queue được xét theo khóa ưu tiên.

`_DispatchEvent` dùng `env.schedule` với priority `NORMAL + 1`. `StageResource` mở rộng hook `_trigger_put` của SimPy, được kiểm tra với phiên bản 4.1.2 trong môi trường dự án.

Trong `make_resources`, dòng tạo pool hiện tại là:

```python
res = StageResource(env, capacity=capacity)
```

Hai class `_DispatchEvent`, `StageResource` đã có trong source. Các đoạn main và tên `make_resources`, `pools`, `res` giữ nguyên.

## 3. process.py: khóa FIFO và log queued

Hàm hiện có thêm tham số nội bộ cuối hàm; các tham số cũ giữ nguyên:

```python
def material_flow(env, material_request, pools, config, events,
                  start_stage='amr', remaining_min=None,
                  initial_priority=None):
    ...
```

Hàm con `request_slot(stage)` xây khóa:

```python
priority = (env.now, 2, material_request.request_id)
if stage == start_stage and initial_priority is not None:
    priority = initial_priority
```

Khóa xét phút đến trước, rồi nhóm WIP/demand, rồi thứ tự snapshot hoặc ID. Nhờ vậy request đã chờ từ phút 1 đứng trước request mới tới phút 5 dù ID lớn hơn. Với job cùng mới tới một stage, request_id tăng dần quyết định thứ tự.

Ba chỗ yêu cầu pool hiện dùng:

```python
with request_slot("amr") as req:
    yield req
    # Giữ đoạn start, duration, timeout, finish hiện tại.

with request_slot("packer") as req:
    yield req
    # Giữ phần phục vụ hiện tại.

with request_slot("agv") as req:
    yield req
    # Giữ phần phục vụ hiện tại.
```

Ba khối `if not req.triggered` cũ đã được bỏ. Callback `on_queued` chỉ ghi queued khi job thực sự chưa được cấp slot sau khi xử lý nhóm tới cùng phút. Job được phục vụ ngay không có queued. Snapshot queued WIP vẫn được simulation ghi từ phút 0 như trước.

## 4. simulation.py: giữ ưu tiên active và FIFO của WIP

Vòng lặp active hiện dùng enumerate và truyền khóa vào `material_flow`:

```python
for index, job in enumerate(initial_state.active_jobs):
    env.process(material_flow(
        env, job.request, pools, config, events,
        start_stage=job.stage,
        remaining_min=job.remaining_min,
        initial_priority=(0, 0, index),
    ))
```

Vòng queued cũng dùng enumerate, giữ phần ghi queued/loại log trùng và truyền khóa:

```python
initial_priority=(0, 1, index)
```

Nhóm 0 là active, nhóm 1 là queued WIP theo thứ tự mảng, nhóm 2 là job mới tới stage. `initial_priority` chỉ áp dụng ở stage bắt đầu của WIP; khi chuyển sang stage tiếp theo, job dùng phút đến và request_id bình thường.

Ở đầu `validate_inputs`, code gọi lại `config.__post_init__()`. Trong vòng duyệt demand, code gọi `request.__post_init__()` trước các kiểm tra horizon/ID. Điều này kiểm tra lại config/request nếu bị sửa sau khi khởi tạo; đặc biệt không cho horizon vô cực đi vào vòng `env.peek()`.

`run`, `SimulationResult` và tên KPI giữ nguyên.

## 5. Chạy nghiệm thu

Chọn Python của môi trường `denso-logtwin`, rồi chạy từ workspace root:

```powershell
python -m pytest -q
python -m src.twin.simulation
python -m examples.phase2_demo
```

Nếu terminal chưa kích hoạt môi trường, dùng đường dẫn Python hiện có trên máy:

```powershell
& 'C:\Users\Acer\miniconda3\envs\denso-logtwin\python.exe' -m pytest -q
& 'C:\Users\Acer\miniconda3\envs\denso-logtwin\python.exe' -m src.twin.simulation
& 'C:\Users\Acer\miniconda3\envs\denso-logtwin\python.exe' -m examples.phase2_demo
```

`tests/test_twin.py` có 44 ca pytest, gồm fixture schema/events, tie-break tại Packer và AGV, WIP/FIFO, capacity tăng, queued nhiều stage xen kẽ, active remaining time, horizon, input lỗi, peak queue giữa hai snapshot, tính độc lập, bảo toàn số job và giới hạn capacity.

`examples.phase2_demo` dùng fixture root `contracts_v0.1_example.json`, kiểm tra KPI rồi xuất `data/results/phase2_simulation_result.json`. File JSON trong thư mục code mẫu đã được tạo để xem trước đầu ra.

## 6. Kết quả đã kiểm tra và điều kiện chốt

- Bản code mẫu: 44 test Twin PASS.
- Bản ghép với Generator, config, contract và test hiện có trong repo: 52 test PASS (44 Twin + 8 hiện có).
- Main A/B: PASS; throughput=2, unfinished=0, avg_total_wait_min=1.5, avg_lead_time_min=13.5, utilization=(0.5, 0.4, 0.3), peak_queue=(1, 0, 0).
- Demo xuất JSON: PASS.

Kiểm tra lại trực tiếp checkout chính cũng cho 52 test PASS, main A/B PASS và demo JSON PASS. Core Phase 2 đủ điều kiện bàn giao theo bộ kiểm tra hiện tại. Gửi bên tích hợp API `from src.twin.simulation import run` và JSON mẫu; việc xác nhận bên nhận sử dụng kết quả chưa được kiểm chứng ở đây. Adapter Generator là bước phối hợp Sync 1; Bottleneck Detector, Scenario Runner, Forecast, Calibration và Backtest thuộc các phase sau.
