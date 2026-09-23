from abc import ABC, abstractmethod

import numpy as np

from .types import PoseFrame


class PoseBackend(ABC):
    """Model-neutral synchronous frame interface."""

    @abstractmethod
    def infer(self, bgr_frame: np.ndarray, timestamp_ms: int, frame_id: int) -> PoseFrame:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError

    def __enter__(self) -> "PoseBackend":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

