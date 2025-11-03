import datetime as _dt
from django.utils import timezone as _tz
from datetime import timedelta
import sys

# --------------------------------------------------------------------------- #
# Break/Lunch deduction helpers (moved here for payroll calc integration)
# --------------------------------------------------------------------------- #
def _ensure_aware(dt: _dt.datetime) -> _dt.datetime:
    """
    Ensure a datetime is timezone-aware.
    - If naive, assume current Django timezone and make it aware.
    - If aware, return as-is.
    """
    if _tz.is_naive(dt):
        return _tz.make_aware(dt, _tz.get_current_timezone())
    return dt

LUNCH_START = _dt.time(12, 0)
LUNCH_END = _dt.time(13, 0)
REST_FIXED = _dt.timedelta(minutes=15)

def _overlap(start1, end1, start2, end2):
    """Return overlap timedelta between [start1,end1] and [start2,end2]."""
    latest_start = max(start1, start2)
    earliest_end = min(end1, end2)
    return max(earliest_end - latest_start, _dt.timedelta())

def calc_daily_duration(cin: _dt.datetime, cout: _dt.datetime) -> _dt.timedelta:
    """
    1日の実働:
      - 12:00〜13:00 を“重なった分だけ”控除
      - さらに常に 15 分控除
      - マイナスは 0 に丸め
    """
    # Accept both naive and aware datetimes:
    start = _tz.localtime(_ensure_aware(cin))
    end   = _tz.localtime(_ensure_aware(cout))
    if end <= start:
        return _dt.timedelta(0)

    dur = end - start

    # その日の12:00-13:00窓を作成（出勤が翌日跨ぎでも cin の日付を基準にする想定）
    noon_s = start.replace(hour=12, minute=0, second=0, microsecond=0)
    noon_e = start.replace(hour=13, minute=0, second=0, microsecond=0)

    dur -= _overlap(start, end, noon_s, noon_e)
    dur -= REST_FIXED

    if dur < _dt.timedelta(0):
        dur = _dt.timedelta(0)
    return dur


# DEBUG: 確認用に呼び出しと出力をコンソール表示
def _h_str(td: timedelta | None) -> str:
    """timedeltaを 'H:MM' 形式に変換"""
    if not td:
        return "0:00"
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    return f"{hours}:{minutes:02d}"