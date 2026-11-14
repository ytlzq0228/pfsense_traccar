"""Read host temperature without loading drivers or changing system settings."""
import math
import os
import re
import subprocess

_SENSOR = re.compile(r'^(dev\.cpu\.\d+\.temperature|hw\.acpi\.thermal\.tz\d+\.temperature):\s*([-+]?\d+(?:\.\d+)?)C\s*$')


def read_temperature():
    """Return the hottest CPU core, otherwise hottest ACPI zone, in Celsius.

    Ignore critical/trip thresholds and unavailable/implausible measurements.
    sysctl may exit nonzero when one requested subtree is absent: valid output
    from the other subtree can still be used.
    """
    try:
        result = subprocess.run(
            ['/sbin/sysctl', 'dev.cpu', 'hw.acpi.thermal'],
            capture_output=True, text=True, encoding='ascii', errors='replace',
            timeout=0.5, env={**os.environ, 'LC_ALL': 'C'})
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    cpu, acpi = [], []
    for line in result.stdout.splitlines():
        match = _SENSOR.fullmatch(line)
        if not match:
            continue
        oid, value = match.groups()
        celsius = float(value)
        if not math.isfinite(celsius) or not -40 <= celsius <= 150:
            continue
        (cpu if oid.startswith('dev.cpu.') else acpi).append(celsius)
    values = cpu or acpi
    return (round(max(values), 1), 'CPU' if cpu else 'ACPI') if values else (None, None)
