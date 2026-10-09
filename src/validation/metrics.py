"""Phase 8: so sánh KPI dự đoán với KPI thực tế."""

from math import isfinite
from numbers import Real

STAGES = ("amr", "packer", "agv")

SCALAR_KPIS = (
    "throughput_count",
    "unfinished_count",
    "avg_total_wait_min",
    "avg_lead_time_min",
)

STAGE_KPIS = (
    "avg_queue_length",
    "peak_queue",
)


def _number(value):
    """Chấp nhận số hữu hạn hoặc None."""
    if value is None:
        return None

    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(
            f"KPI phải là số hoặc None, nhận được: {value!r}"
        )

    value = float(value)

    if not isfinite(value):
        raise ValueError("KPI phải là số hữu hạn")

    return value


def calculate_error(actual, predicted):
    """Tính sai số tuyệt đối và sai số tương đối (%)."""
    actual = _number(actual)
    predicted = _number(predicted)

    absolute_error = None
    relative_error_pct = None

    if actual is not None and predicted is not None:
        absolute_error = abs(predicted - actual)

        # Actual = 0: không chia được để tính phần trăm.
        # Vẫn giữ sai số tuyệt đối để đánh giá.
        if actual != 0:
            relative_error_pct = (
                absolute_error / abs(actual) * 100.0
            )

    return {
        "actual": actual,
        "predicted": predicted,
        "absolute_error": absolute_error,
        "relative_error_pct": relative_error_pct,
    }


def compare_metrics(actual: dict, predicted: dict) -> dict:
    """So sánh KPI cùng cửa sổ thời gian và cùng quy ước tính."""
    errors = {
        name: calculate_error(
            actual[name],
            predicted[name],
        )
        for name in SCALAR_KPIS
    }

    for name in STAGE_KPIS:
        errors[name] = {
            stage: calculate_error(
                actual[name][stage],
                predicted[name][stage],
            )
            for stage in STAGES
        }

    return errors