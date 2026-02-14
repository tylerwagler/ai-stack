
import pynvml
import psutil
import time
import threading
import logging
import os

logger = logging.getLogger(__name__)

class HardwareMonitor:
    def __init__(self):
        try:
            pynvml.nvmlInit()
            self.device_count = pynvml.nvmlDeviceGetCount()
            self.handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(self.device_count)]
            logger.info(f"Initialized NVML with {self.device_count} devices.")
        except pynvml.NVMLError as e:
            logger.error(f"Failed to initialize NVML: {e}")
            self.device_count = 0
            self.handles = []

    def get_host_metrics(self):
        cpu_percent = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory()
        load_avg = os.getloadavg()
        uptime = int(time.time() - psutil.boot_time())
        
        return {
            "hostname": os.uname().nodename,
            "cpu_load_percent": cpu_percent,
            "memory_total_mb": int(mem.total / (1024 * 1024)),
            "memory_available_mb": int(mem.available / (1024 * 1024)),
            "load_avg_1m": load_avg[0],
            "load_avg_5m": load_avg[1],
            "load_avg_15m": load_avg[2],
            "uptime_seconds": uptime
        }

    def get_gpu_metrics(self):
        metrics = []
        for i, handle in enumerate(self.handles):
            try:
                name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode('utf-8')
                
                try:
                    serial = pynvml.nvmlDeviceGetSerial(handle)
                    if isinstance(serial, bytes): serial = serial.decode('utf-8')
                except:
                    serial = "N/A"

                try:
                    vbios = pynvml.nvmlDeviceGetVbiosVersion(handle)
                    if isinstance(vbios, bytes): vbios = vbios.decode('utf-8')
                except:
                    vbios = "N/A"

                temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                
                # Utilization
                try:
                    util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                    gpu_util = util.gpu
                    mem_util = util.memory
                except:
                    gpu_util = 0
                    mem_util = 0

                try:
                    mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    mem_total = int(mem_info.total / (1024 * 1024)) # MB
                    mem_used = int(mem_info.used / (1024 * 1024))   # MB
                except:
                    mem_total = 0
                    mem_used = 0
                
                # Power
                try:
                    power_usage = pynvml.nvmlDeviceGetPowerUsage(handle) # mW
                except:
                    power_usage = 0

                try:
                    power_limit = pynvml.nvmlDeviceGetEnforcedPowerLimit(handle) # mW
                except:
                    power_limit = 0

                # Fan
                try:
                    fan_speed = pynvml.nvmlDeviceGetFanSpeed(handle)
                except:
                    fan_speed = 0

                # Clocks
                try:
                    graphics_clock = pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_GRAPHICS)
                    mem_clock = pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_MEM)
                    sm_clock = pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_SM)
                    video_clock = pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_VIDEO)
                except:
                    graphics_clock = 0
                    mem_clock = 0
                    sm_clock = 0
                    video_clock = 0
                
                # Max Clocks
                try:
                    max_graphics = pynvml.nvmlDeviceGetMaxClockInfo(handle, pynvml.NVML_CLOCK_GRAPHICS)
                    max_mem = pynvml.nvmlDeviceGetMaxClockInfo(handle, pynvml.NVML_CLOCK_MEM)
                except:
                    max_graphics = 0
                    max_mem = 0

                # PCIe
                try:
                    pcie_tx = pynvml.nvmlDeviceGetPcieThroughput(handle, pynvml.NVML_PCIE_UTIL_TX_BYTES)
                    pcie_rx = pynvml.nvmlDeviceGetPcieThroughput(handle, pynvml.NVML_PCIE_UTIL_RX_BYTES)
                    pcie_gen = pynvml.nvmlDeviceGetCurrPcieLinkGeneration(handle)
                    pcie_width = pynvml.nvmlDeviceGetCurrPcieLinkWidth(handle)
                except:
                    pcie_tx = 0
                    pcie_rx = 0
                    pcie_gen = 0
                    pcie_width = 0
                
                # Processes
                procs = []
                try:
                    # Get compute processes
                    compute_procs = pynvml.nvmlDeviceGetComputeRunningProcesses(handle)
                    for p in compute_procs:
                        try:
                            p_name = pynvml.nvmlSystemGetProcessName(p.pid).decode('utf-8')
                        except:
                            p_name = "Unknown"
                        procs.append({"pid": p.pid, "used_memory": int(p.usedGpuMemory / (1024*1024)), "name": p_name})
                        
                    # Get graphics processes
                    graphics_procs = pynvml.nvmlDeviceGetGraphicsRunningProcesses(handle)
                    for p in graphics_procs:
                        try:
                            p_name = pynvml.nvmlSystemGetProcessName(p.pid).decode('utf-8')
                        except:
                            p_name = "Unknown"
                        # Avoid duplicates
                        if not any(x['pid'] == p.pid for x in procs):
                            procs.append({"pid": p.pid, "used_memory": int(p.usedGpuMemory / (1024*1024)), "name": p_name})
                except:
                    pass

                metrics.append({
                    "index": i,
                    "name": name,
                    "serial": serial,
                    "vbios": vbios,
                    "p_state": {
                        "id": 0, # Placeholder
                        "description": "P0" # Placeholder
                    },
                    "temperature": temp,
                    "fan_speed_percent": fan_speed,
                    "target_fan_percent": fan_speed, # Placeholder, assume equal for now
                    "power_usage_mw": power_usage,
                    "power_limit_mw": power_limit,
                    "resources": {
                        "gpu_load_percent": gpu_util,
                        "memory_load_percent": mem_util,
                        "memory_used_mb": mem_used,
                        "memory_total_mb": mem_total
                    },
                    "clocks": {
                        "graphics": graphics_clock,
                        "memory": mem_clock,
                        "sm": sm_clock,
                        "video": video_clock,
                        "max_graphics": max_graphics,
                        "max_memory": max_mem
                    },
                    "pcie": {
                        "tx_throughput_kbs": int(pcie_tx / 1024),
                        "rx_throughput_kbs": int(pcie_rx / 1024),
                        "gen": pcie_gen,
                        "width": pcie_width
                    },
                    "ecc": {
                        "volatile_single": 0,
                        "volatile_double": 0,
                        "aggregate_single": 0,
                        "aggregate_double": 0
                    },
                    "processes": procs,
                    "throttle_alert": "None"
                })
            except pynvml.NVMLError as e:
                logger.error(f"Error reading GPU {i}: {e}")
                
        return metrics

    def set_fan_speed(self, index, speed_percent):
        if index < 0 or index >= len(self.handles):
            return False
        try:
            handle = self.handles[index]
            # Set for all fans on the device
            num_fans = 0
            try:
                num_fans = pynvml.nvmlDeviceGetNumFans(handle)
            except:
                num_fans = 1 # Fallback
                
            for i in range(num_fans):
                pynvml.nvmlDeviceSetFanSpeed_v2(handle, i, int(speed_percent))
            return True
        except pynvml.NVMLError as e:
            logger.error(f"Failed to set fan speed for GPU {index}: {e}")
            return False
            
    def set_auto_fan(self, index):
        if index < 0 or index >= len(self.handles):
            return False
        try:
            handle = self.handles[index]
            num_fans = 0
            try:
                num_fans = pynvml.nvmlDeviceGetNumFans(handle)
            except:
                num_fans = 1

            for i in range(num_fans):
                pynvml.nvmlDeviceSetFanControlPolicy(handle, i, pynvml.NVML_FAN_POLICY_TEMPERATURE_CONTINOUS_SW)
            return True
        except pynvml.NVMLError as e:
             # Fallback for older cards/drivers that don't support policy
            try:
                # Some cards reject setting policy but might accept "default" behavior? 
                # Actually NVML doesn't have a clear "auto" function for consumer cards once manual is set, 
                # except reboot or driver reset. But `NVML_FAN_POLICY_TEMPERATURE_CONTINOUS_SW` is the standard way.
                logger.error(f"Failed to set auto fan for GPU {index}: {e}")
                return False
            except:
                return False

    def check_throttle(self, index):
        if index < 0 or index >= len(self.handles):
            return 0
        try:
            handle = self.handles[index]
            reasons = pynvml.nvmlDeviceGetCurrentClocksEventReasons(handle)
            return reasons
        except:
            return 0

class FanController(threading.Thread):
    def __init__(self, monitor: HardwareMonitor):
        super().__init__()
        self.monitor = monitor
        self.running = True
        self.points = []
        self.parse_setpoints(os.environ.get("FAN_SETPOINTS", "50:30 70:65 78:95 80:100"))

    def parse_setpoints(self, setpoint_str):
        if not setpoint_str: return
        try:
            pairs = setpoint_str.split()
            pts = []
            for p in pairs:
                temp, speed = map(int, p.split(':'))
                pts.append((temp, speed))
            self.points = sorted(pts, key=lambda x: x[0])
            logger.info(f"Fan curve loaded: {self.points}")
        except Exception as e:
            logger.error(f"Error parsing fan setpoints: {e}")

    def interpolate(self, temp):
        if not self.points: return 0
        if temp <= self.points[0][0]: return self.points[0][1]
        if temp >= self.points[-1][0]: return self.points[-1][1]
        
        for i in range(len(self.points) - 1):
            t1, s1 = self.points[i]
            t2, s2 = self.points[i+1]
            if t1 <= temp <= t2:
                ratio = (temp - t1) / (t2 - t1)
                return int(s1 + ratio * (s2 - s1))
        return 0

    def run(self):
        logger.info("Fan controller started.")
        while self.running:
            try:
                metrics = self.monitor.get_gpu_metrics()
                for gpu in metrics:
                    temp = gpu['temperature']
                    target_speed = self.interpolate(temp)
                    # We inject the target fan speed into the metric dict for display (though this dict is local)
                    # Ideally we write to a shared state, but verify via get_fan_speed
                    
                    self.monitor.set_fan_speed(gpu['index'], target_speed)
            except Exception as e:
                logger.error(f"Error in fan control loop: {e}")
            
            time.sleep(2) # Update every 2 seconds

    def stop(self):
        self.running = False
