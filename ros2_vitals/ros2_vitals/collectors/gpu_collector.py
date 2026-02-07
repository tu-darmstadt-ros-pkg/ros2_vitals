"""Collector for NVIDIA GPU statistics via nvidia-ml-py (pynvml)."""

from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

# Try to import pynvml from nvidia-ml-py, but don't fail if not available
_pynvml_available = False
_nvml_initialized = False

try:
    # nvidia-ml-py provides the pynvml module
    from pynvml import nvmlInit, nvmlShutdown, nvmlDeviceGetCount
    from pynvml import nvmlDeviceGetHandleByIndex, nvmlDeviceGetName
    from pynvml import nvmlDeviceGetUtilizationRates, nvmlDeviceGetMemoryInfo
    from pynvml import nvmlDeviceGetTemperature, NVML_TEMPERATURE_GPU
    from pynvml import nvmlDeviceGetPowerUsage, nvmlDeviceGetFanSpeed
    from pynvml import nvmlDeviceGetComputeRunningProcesses
    _pynvml_available = True
except ImportError:
    pass


class GpuCollector:
    """
    Collects NVIDIA GPU statistics using nvidia-ml-py (pynvml).

    If nvidia-ml-py is not installed or no NVIDIA GPU is available,
    returns empty results gracefully.
    """

    def __init__(self):
        self._available = False
        self._device_count = 0
        self._initialize()

    def _initialize(self):
        """Initialize NVML library."""
        global _nvml_initialized

        if not _pynvml_available:
            logger.debug("nvidia-ml-py not installed, GPU monitoring disabled")
            return

        if _nvml_initialized:
            self._available = True
            try:
                self._device_count = nvmlDeviceGetCount()
            except Exception:
                self._device_count = 0
            return

        try:
            nvmlInit()
            _nvml_initialized = True
            self._device_count = nvmlDeviceGetCount()
            self._available = self._device_count > 0
            if self._available:
                logger.info(f"NVML initialized, found {self._device_count} GPU(s)")
            else:
                logger.debug("NVML initialized but no GPUs found")
        except Exception as e:
            logger.debug(f"Failed to initialize NVML: {e}")
            self._available = False

    @property
    def available(self) -> bool:
        """Check if GPU monitoring is available."""
        return self._available

    def get_gpus(self) -> List[Dict[str, Any]]:
        """
        Get statistics for all NVIDIA GPUs.

        Returns:
            List of dictionaries with GPU statistics
        """
        if not self._available:
            return []

        gpus = []

        for i in range(self._device_count):
            try:
                handle = nvmlDeviceGetHandleByIndex(i)
                gpu_info = self._get_gpu_info(i, handle)
                if gpu_info:
                    gpus.append(gpu_info)
            except Exception as e:
                logger.debug(f"Failed to get info for GPU {i}: {e}")

        return gpus

    def _get_gpu_info(self, index: int, handle) -> Optional[Dict[str, Any]]:
        """Get info for a single GPU."""
        try:
            # Name
            name = nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode('utf-8')

            # Utilization
            try:
                util = nvmlDeviceGetUtilizationRates(handle)
                utilization = float(util.gpu)
                logger.debug(f"GPU {index} utilization: {utilization}% (memory util: {util.memory}%)")
            except Exception as e:
                logger.debug(f"Failed to get GPU {index} utilization: {e}")
                utilization = 0.0

            # Memory
            try:
                mem = nvmlDeviceGetMemoryInfo(handle)
                memory_total = mem.total
                memory_used = mem.used
            except Exception:
                memory_total = 0
                memory_used = 0

            # Temperature
            try:
                temperature = float(nvmlDeviceGetTemperature(
                    handle, NVML_TEMPERATURE_GPU
                ))
            except Exception:
                temperature = -1.0

            # Power
            try:
                power_mw = nvmlDeviceGetPowerUsage(handle)
                power_watts = power_mw // 1000
            except Exception:
                power_watts = -1

            # Fan speed
            try:
                fan_speed = nvmlDeviceGetFanSpeed(handle)
            except Exception:
                fan_speed = -1

            return {
                'index': index,
                'name': name,
                'utilization_percent': utilization,
                'memory_total_bytes': memory_total,
                'memory_used_bytes': memory_used,
                'temperature_celsius': temperature,
                'power_watts': power_watts,
                'fan_speed_percent': fan_speed,
            }
        except Exception as e:
            logger.debug(f"Error getting GPU {index} info: {e}")
            return None

    def get_process_gpu_memory(self, pid: int) -> Optional[Dict[str, Any]]:
        """
        Get GPU memory usage for a specific process.

        Args:
            pid: Process ID

        Returns:
            Dictionary with gpu_index and memory_bytes, or None if not using GPU
        """
        if not self._available:
            return None

        for i in range(self._device_count):
            try:
                handle = nvmlDeviceGetHandleByIndex(i)
                processes = nvmlDeviceGetComputeRunningProcesses(handle)

                for proc in processes:
                    if proc.pid == pid:
                        return {
                            'gpu_index': i,
                            'memory_bytes': proc.usedGpuMemory or 0,
                        }
            except Exception:
                continue

        return None

    def shutdown(self):
        """Shutdown NVML library."""
        global _nvml_initialized
        if _nvml_initialized and _pynvml_available:
            try:
                nvmlShutdown()
                _nvml_initialized = False
            except Exception:
                pass
