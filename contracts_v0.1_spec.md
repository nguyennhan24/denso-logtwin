# Digital Twin Control Tower — data contracts v0.1

**Trạng thái:** Đặc tả do Nhân chốt để triển khai v0.1. Ví dụ đi kèm: `contracts_v0.1_example.json`. Bản làm rõ thứ tự queue ngày 2026-09-26 giữ nguyên các trường và version 0.1, trước khi tích hợp engine.

## 1. Quy ước chung

- Mỗi `MaterialRequest` là **một lượt giao vật tư**. `quantity` là số lượng vật tư nguyên dương; ở v0.1 nó không làm thay đổi thời gian phục vụ.
- Luồng cố định: `amr` → `packer` → `agv` → giao tới dây chuyền (`line`). Mỗi công đoạn phục vụ một request tại một thời điểm trên một đơn vị tài nguyên. Mỗi stage có một hàng đợi FIFO; quy tắc ưu tiên khi cùng thời điểm được ghi ở mục 3, **không sắp xếp lại WIP theo `request_id` hay `arrival_min`**.
- Thời gian mô phỏng là **phút** dạng số (`number`); thời điểm bắt đầu chạy là `time_min = 0`. Timestamp ngoài đời là chuỗi ISO 8601 có múi giờ, ví dụ `2026-09-25T09:00:00+07:00`.
- ID là chuỗi không rỗng, duy nhất cho request trong một lần chạy. `origin_id`, `destination_line_id`, `material_id` là ID chuẩn sau bước mapping của Data Layer.
- Đầu vào không thay đổi trong khi `run()` thực thi. Hai lần chạy cùng config, demand, initial state và seed phải cho cùng kết quả. Dùng dấu chấm (`.`) trong JSON cho số thập phân.

## 2. MaterialRequest

| Trường | Kiểu JSON | Điều kiện | Ý nghĩa |
| --- | --- | --- | --- |
| `request_id` | string | Bắt buộc, duy nhất | ID chuẩn của lượt giao |
| `arrival_min` | number | ≥ 0 và < `horizon_min` đối với demand mới | Phút xuất hiện tính từ lúc bắt đầu chạy |
| `origin_id` | string | Bắt buộc | Điểm lấy vật tư |
| `destination_line_id` | string | Bắt buộc | Dây chuyền nhận |
| `material_id` | string | Bắt buộc | Loại vật tư |
| `quantity` | integer | > 0 | Lượng vật tư của lượt giao |

Request có sẵn trong `initial_state` dùng `arrival_min = 0` chỉ để tính các KPI trong cửa sổ quan sát. Giá trị này **không quyết định vị trí trong hàng đợi**: WIP đã có mặt trước `time_min = 0`; các KPI về chờ và lead time không suy đoán thời gian trước snapshot.

## 3. CurrentLogisticsState

`as_of` là thời điểm chụp hệ thống và là mốc `time_min = 0` khi state được đưa vào Twin.

| Trường | Kiểu JSON | Ý nghĩa |
| --- | --- | --- |
| `as_of` | string (ISO 8601 + timezone) | Thời điểm chụp |
| `resources` | object theo ba key `amr`, `packer`, `agv` | Mỗi key có `total`, `busy`, `offline` là số nguyên ≥ 0; `busy + offline ≤ total` |
| `queued_jobs` | array | Mỗi phần tử có `stage` và `request` (MaterialRequest); vị trí trong mảng là thứ tự FIFO của WIP trong từng stage |
| `active_jobs` | array | Mỗi phần tử có `stage`, `remaining_min` > 0 và `request` (MaterialRequest) |
| `feed_updated_at` | object | 6 timestamp: `wms`, `amr`, `agv`, `production`, `labor`, `excel`; timestamp không vượt quá `as_of` |

`stage` chỉ nhận `amr`, `packer`, `agv`. `busy` tại từng stage bằng số active job ở stage đó. Số đang xếp hàng của NOW được tính từ `queued_jobs` thay vì lưu thêm một số đếm dễ lệch. Các request trong `queued_jobs`, `active_jobs` và `demand` phải có ID khác nhau.

**Thứ tự đưa job vào mỗi hàng đợi khi bắt đầu chạy:**

1. Nạp các `active_jobs` vào tài nguyên đang bận; chúng đã được phục vụ từ trước snapshot.
2. Nạp `queued_jobs` vào từng stage theo **đúng thứ tự xuất hiện trong mảng**, kể cả khi mảng chứa nhiều stage xen kẽ. WIP đứng trước mọi job mới vào cùng stage ở phút 0. Nếu scenario tăng capacity làm xuất hiện tài nguyên rảnh ở phút 0, cấp tài nguyên đó cho WIP theo thứ tự mảng trước.
3. Sau đó mới nhận `demand` mới có `arrival_min = 0`. Các demand mới đến cùng phút và cùng stage được xét theo `request_id` tăng dần, **sau toàn bộ WIP đã xếp hàng ở stage đó**. Với demand đến sau phút 0, job đã chờ trong hàng đợi luôn đứng trước job mới; nếu nhiều job cùng mới vào một stage tại một thời điểm thì xét `request_id` tăng dần.

Ví dụ: AMR có 1 xe đang bận thêm 3 phút; `queued_jobs` lần lượt chứa `REQ-OLD-002`, `REQ-OLD-001`; demand mới `REQ-001` đến phút 0; AMR phục vụ mỗi job 5 phút. Thứ tự **bắt đầu AMR sau snapshot** là `REQ-OLD-002` phút 3 → `REQ-OLD-001` phút 8 → `REQ-001` phút 13. Dù ID và `arrival_min` gợi thứ tự khác, không được sắp lại WIP. Ca này nằm trong `ordering_check` của JSON mẫu; `ordering_check` là dữ liệu kiểm tra của fixture, không phải trường thuộc 4 contract.

Khi dùng state để chạy Twin, `ScenarioConfig.capacity` là **số đơn vị có thể phục vụ trong scenario**. Với baseline, capacity bằng `total - offline` tại snapshot và không nhỏ hơn `busy`. Scenario WHAT-IF có thể tăng capacity so với baseline, nhưng không được thấp hơn số đang bận. Job đang active tiếp tục ở stage hiện tại trong `remaining_min`; job đang queue chờ stage đã ghi. Feed timestamp dành cho NOW/freshness, không phải thời gian mô phỏng.

## 4. ScenarioConfig

| Trường | Kiểu JSON | Điều kiện và ý nghĩa |
| --- | --- | --- |
| `scenario_id` | string | Tên duy nhất để gắn result với scenario |
| `horizon_min` | number | > 0; độ dài cửa sổ mô phỏng |
| `sample_every_min` | number | > 0 và chia hết `horizon_min`; bước lấy mẫu cho timeline |
| `random_seed` | integer | Seed để lặp lại kết quả; v0.1 dùng thời gian phục vụ cố định |
| `capacity` | object | Key `amr`, `packer`, `agv`, mỗi giá trị là integer > 0 |
| `service_min` | object | Key `amr`, `packer`, `agv`, mỗi giá trị là number > 0; phút phục vụ cho một request |

Ví dụ `+2 AMR` là sao chép config baseline và tăng `capacity.amr` thêm 2; không sửa config gốc. V0.1 không ưu tiên request, không routing và không tính thời gian phục vụ theo quantity.

## 5. Giao diện Twin và SimulationResult

```python
def run(
    config: ScenarioConfig,
    demand: list[MaterialRequest],
    initial_state: CurrentLogisticsState | None = None,
) -> SimulationResult:
    ...
```

`demand` chỉ chứa **request mới trong thời gian dự báo**. Nếu `initial_state is None`, Twin bắt đầu với tài nguyên rảnh và hàng đợi rỗng. Nếu có state, Twin nạp active/queued jobs theo mục 3. Ví dụ JSON đi kèm minh họa `initial_state=None` cho run 20 phút; snapshot trong cùng file là ví dụ contract NOW riêng, không phải đầu vào của run ví dụ.

`SimulationResult` gồm `scenario_id`, `horizon_min`, `initial_state_used` (boolean), `input_count` (số request mới), `metrics`, `timeline`, `events`:

| Trường trong `metrics` | Quy tắc |
| --- | --- |
| `throughput_count` | Số request giao tới `line` trong cửa sổ `[0, horizon_min]`, gồm cả WIP nếu có initial state |
| `unfinished_count` | Tổng số request mới + WIP trừ `throughput_count` |
| `avg_total_wait_min` | Trung bình tổng thời gian chờ ba stage của các request đã giao; không có request nào giao thì `null` |
| `avg_lead_time_min` | Trung bình `delivered_min - arrival_min` của các request đã giao; với WIP, `arrival_min=0` để đo từ snapshot; không có request giao thì `null` |
| `utilization` | Theo stage, `busy_resource_minutes / (capacity × horizon_min)`, từ 0 đến 1 |
| `peak_queue` | Độ dài hàng đợi cao nhất theo từng stage từ **toàn bộ sự kiện**, kể cả giữa hai mốc timeline |

`timeline` là array tại `time_min = 0, sample_every_min, ... horizon_min`. Mỗi điểm chứa `queue` (số chờ ở `amr`, `packer`, `agv`) và `completed_count` lũy kế. Tại cùng một thời điểm, xử lý toàn bộ sự kiện rồi mới ghi điểm timeline.

`events` là array theo thời gian tăng dần, mỗi phần tử gồm `time_min`, `request_id`, `stage`, `event`. Giá trị `event`: `arrival`, `queued`, `start`, `finish`, `delivered`. `delivered` dùng `stage="line"`. Một request được tính hoàn thành **một lần**, khi có `delivered`. Event ở đúng `horizon_min` được tính; request đến mới phải có `arrival_min < horizon_min`.

## 6. Quy tắc kiểm tra và bàn giao

1. JSON parse được; có đúng ba key capacity/service và đúng sáu key `feed_updated_at`.
2. Không có ID trùng giữa demand và WIP; mọi stage hợp lệ; `busy` khớp số active job; capacity baseline khớp tài nguyên online. Thứ tự của `queued_jobs` là dữ liệu có nghĩa, không được dùng `set`, sort hoặc groupby làm mất thứ tự.
3. Chạy cùng input hai lần ra cùng result; chạy scenario khác bằng bản sao config để không sửa baseline.
4. Ví dụ rỗng state với hai request trong fixture phải cho: `throughput_count=2`, `avg_total_wait_min=1.5`, utilization AMR `0.5`, Packer `0.4`, AGV `0.3`.
5. Chạy `ordering_check` với state có sẵn và chỉ `REQ-001` trong demand phải cho ba mốc bắt đầu AMR sau snapshot là 3, 8, 13 theo đúng thứ tự nêu ở mục 3. Đây là ca kiểm tra phân biệt WIP với demand cùng `arrival_min=0`.
6. File giao cho team: spec này và fixture JSON. Trung xuất `MaterialRequest`/`CurrentLogisticsState`; Nhân xuất `SimulationResult`; Cường xuất `ScenarioConfig`; Hải Anh chỉ đọc các contract đã thống nhất.

**Quy tắc sửa:** Mọi thay đổi tên trường, kiểu, đơn vị hoặc ý nghĩa KPI phải tăng version (ví dụ 0.2), cập nhật cả spec và fixture, rồi thông báo người dùng của contract đó. Chỉnh giá trị ví dụ mà không đổi cấu trúc chỉ cần ghi vào change-log.
