"""Rate limiting por token bucket, em memória.

Sem Redis nesta versão — a interface fica pronta para plugar depois. Para um
servidor stdio de uso local (o caso da IDE) memória é suficiente; para o modo
HTTP com múltiplos consumidores, um backend distribuído passa a fazer sentido.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class TokenBucketLimiter:
    """Um bucket por chave, reabastecido continuamente."""

    def __init__(self, *, capacity: int, refill_per_minute: int | None = None) -> None:
        self.capacity = float(capacity)
        self.refill_rate = (refill_per_minute or capacity) / 60.0
        self._buckets: dict[str, _Bucket] = {}

    def check(self, key: str, *, cost: float = 1.0) -> tuple[bool, float]:
        """Consome `cost` do bucket de `key`.

        Devolve `(permitido, segundos_para_proxima)`. Quando negado, o segundo
        valor diz quanto esperar — informação que a mensagem de erro usa.
        """
        now = time.monotonic()
        bucket = self._buckets.get(key)

        if bucket is None:
            bucket = _Bucket(tokens=self.capacity, updated_at=now)
            self._buckets[key] = bucket
        else:
            elapsed = now - bucket.updated_at
            bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.refill_rate)
            bucket.updated_at = now

        if bucket.tokens >= cost:
            bucket.tokens -= cost
            return True, 0.0

        deficit = cost - bucket.tokens
        return False, deficit / self.refill_rate if self.refill_rate else 60.0

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self._buckets.clear()
        else:
            self._buckets.pop(key, None)
