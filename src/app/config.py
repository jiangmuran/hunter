from dataclasses import dataclass


@dataclass(frozen=True)
class AppConfig:
    mode: str = "mock"
    base_url: str = "http://192.168.0.170:8000"
    tick_interval: float = 0.1
    align_threshold: float = 0.15
    stop_size_ratio: float = 0.38
    missing_limit: int = 4
