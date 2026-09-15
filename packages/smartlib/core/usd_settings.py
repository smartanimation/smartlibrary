"""Project-wide USD stage conventions, shared by DCC exporters."""
import math


def usd_settings(settings):
    data = settings.get('usd', {})
    if not isinstance(data, dict):
        raise ValueError('USD settings must be a mapping.')
    unit = data.get('meters_per_unit', 0.01)
    if isinstance(unit, bool):
        raise ValueError('USD meters_per_unit must be a positive finite number.')
    try:
        unit = float(unit)
    except (ValueError, TypeError) as exc:
        raise ValueError('USD meters_per_unit must be a positive finite number.') from exc
    if not math.isfinite(unit) or unit <= 0:
        raise ValueError('USD meters_per_unit must be a positive finite number.')
    axis = str(data.get('up_axis', 'Y')).upper()
    if axis not in {'Y', 'Z'}:
        raise ValueError('USD up_axis must be Y or Z.')
    return {'meters_per_unit': unit, 'up_axis': axis}
