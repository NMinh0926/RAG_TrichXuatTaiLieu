"""Container resource sampling for benchmarks, from cgroup v2 files (no extra dependency).

Memory is the whole container (model weights, PyMuPDF buffers, Tesseract subprocesses); CPU is reported
in cores (1.0 = one logical CPU fully busy).
"""
import threading
import time
from pathlib import Path

CGROUP = Path('/sys/fs/cgroup')


def read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def cpu_usec() -> int | None:
    try:
        for line in (CGROUP / 'cpu.stat').read_text().splitlines():
            if line.startswith('usage_usec'):
                return int(line.split()[1])
    except OSError:
        pass
    return None


def directory_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in Path(path).rglob('*') if item.is_file())


class Sampler:
    """Samples container memory and CPU every `interval` seconds while active (use as a context manager)."""

    def __init__(self, interval: float = 0.5):
        self.interval = interval
        self.memory: list[int] = []
        self.cores: list[float] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        last_cpu, last_time = cpu_usec(), time.monotonic()
        while not self._stop.wait(self.interval):
            memory, cpu, now = read_int(CGROUP / 'memory.current'), cpu_usec(), time.monotonic()
            if memory is not None:
                self.memory.append(memory)
            if cpu is not None and last_cpu is not None:
                self.cores.append((cpu - last_cpu) / 1e6 / (now - last_time))
            last_cpu, last_time = cpu, now

    def __enter__(self) -> 'Sampler':
        self.start_cpu, self.started = cpu_usec(), time.monotonic()
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join()
        self.end_cpu, self.ended = cpu_usec(), time.monotonic()

    def summary(self) -> dict:
        elapsed = self.ended - self.started
        average = ((self.end_cpu - self.start_cpu) / 1e6 / elapsed
                   if self.start_cpu is not None and self.end_cpu is not None and elapsed else None)
        return {'peak_memory_mb': round(max(self.memory) / 2**20) if self.memory else None,
                'cpu_cores_average': round(average, 2) if average is not None else None,
                'cpu_cores_peak': round(max(self.cores), 2) if self.cores else None}
