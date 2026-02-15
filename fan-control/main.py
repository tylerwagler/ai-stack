"""
fan-control: Active GPU fan and power limit controller.
Reads GPU temps, interpolates curves, writes fan speeds and power limits.
Runs only on Ellie (or whichever host needs active thermal management).
"""

import os
import signal
import sys
import time

import pynvml

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
FAN_SETPOINTS = os.environ.get("FAN_SETPOINTS", "50:30 70:65 78:95 80:100")
POWER_SETPOINTS = os.environ.get("POWER_SETPOINTS", "")
POLL_INTERVAL = 2.0  # seconds


# ---------------------------------------------------------------------------
# Curve Interpolation
# ---------------------------------------------------------------------------
def parse_curve(raw):
    """Parse 'temp:value temp:value ...' into sorted list of (temp, value) tuples."""
    if not raw or not raw.strip():
        return []
    points = []
    for pair in raw.split():
        try:
            t, v = pair.split(":")
            points.append((int(t), int(v)))
        except ValueError:
            print(f"Warning: ignoring invalid setpoint '{pair}'", file=sys.stderr)
    return sorted(points, key=lambda p: p[0])


def interpolate(points, temp):
    """Linear interpolation between curve points."""
    if not points:
        return None
    if temp <= points[0][0]:
        return points[0][1]
    if temp >= points[-1][0]:
        return points[-1][1]
    for i in range(len(points) - 1):
        t1, v1 = points[i]
        t2, v2 = points[i + 1]
        if t1 <= temp <= t2:
            ratio = (temp - t1) / (t2 - t1)
            return int(v1 + ratio * (v2 - v1))
    return points[-1][1]


# ---------------------------------------------------------------------------
# GPU Controller
# ---------------------------------------------------------------------------
class GPUController:
    def __init__(self):
        pynvml.nvmlInit()
        self.device_count = pynvml.nvmlDeviceGetCount()
        self.handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(self.device_count)]
        print(f"NVML initialized: {self.device_count} GPU(s)", file=sys.stderr)

    def get_temp(self, handle):
        return pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)

    def set_fan_speed(self, handle, speed_percent):
        try:
            num_fans = 1
            try:
                num_fans = pynvml.nvmlDeviceGetNumFans(handle)
            except Exception:
                pass
            for fan in range(num_fans):
                pynvml.nvmlDeviceSetFanSpeed_v2(handle, fan, int(speed_percent))
        except pynvml.NVMLError as e:
            print(f"Failed to set fan speed: {e}", file=sys.stderr)

    def set_power_limit(self, handle, watts):
        try:
            pynvml.nvmlDeviceSetPowerManagementLimit(handle, int(watts * 1000))  # API takes mW
        except pynvml.NVMLError as e:
            print(f"Failed to set power limit: {e}", file=sys.stderr)

    def restore_auto_fans(self):
        """Attempt to restore automatic fan control on all GPUs."""
        for i, handle in enumerate(self.handles):
            try:
                num_fans = 1
                try:
                    num_fans = pynvml.nvmlDeviceGetNumFans(handle)
                except Exception:
                    pass
                for fan in range(num_fans):
                    pynvml.nvmlDeviceSetDefaultFanSpeed_v2(handle, fan)
                print(f"GPU {i}: restored auto fan control", file=sys.stderr)
            except pynvml.NVMLError:
                try:
                    # Fallback: set to temperature-continuous policy
                    num_fans = pynvml.nvmlDeviceGetNumFans(handle) if hasattr(pynvml, 'nvmlDeviceGetNumFans') else 1
                    for fan in range(num_fans):
                        pynvml.nvmlDeviceSetFanControlPolicy(handle, fan, 0)  # 0 = default/auto
                    print(f"GPU {i}: restored fan policy", file=sys.stderr)
                except Exception as e2:
                    print(f"GPU {i}: could not restore auto fan: {e2}", file=sys.stderr)

    def shutdown(self):
        self.restore_auto_fans()
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main Control Loop
# ---------------------------------------------------------------------------
def main():
    fan_curve = parse_curve(FAN_SETPOINTS)
    power_curve = parse_curve(POWER_SETPOINTS)

    if not fan_curve:
        print("ERROR: No valid FAN_SETPOINTS configured", file=sys.stderr)
        sys.exit(1)

    print(f"Fan curve: {fan_curve}", file=sys.stderr)
    if power_curve:
        print(f"Power curve: {power_curve}", file=sys.stderr)
    else:
        print("No power curve configured (POWER_SETPOINTS empty)", file=sys.stderr)

    controller = GPUController()

    # Graceful shutdown: restore auto fans
    def shutdown(sig, frame):
        print("\nShutting down, restoring auto fan control...", file=sys.stderr)
        controller.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print(f"Fan control active, polling every {POLL_INTERVAL}s", file=sys.stderr)

    while True:
        for i, handle in enumerate(controller.handles):
            try:
                temp = controller.get_temp(handle)

                # Fan control
                target_fan = interpolate(fan_curve, temp)
                if target_fan is not None:
                    controller.set_fan_speed(handle, target_fan)

                # Power limit control
                if power_curve:
                    target_power = interpolate(power_curve, temp)
                    if target_power is not None:
                        controller.set_power_limit(handle, target_power)

            except pynvml.NVMLError as e:
                print(f"GPU {i} control error: {e}", file=sys.stderr)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
