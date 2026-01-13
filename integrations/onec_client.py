"""HTTP-клиент для интеграции с 1С (заготовка).

Ожидаемый стиль интеграции: HTTP API + JSON (как вы и писали раньше).
"""

import logging
from typing import Dict, Any, Optional
import requests

logger = logging.getLogger(__name__)

class OneCClient:
    def __init__(self, base_url: str, token: Optional[str] = None, timeout: int = 30):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def ping(self) -> bool:
        url = f"{self.base_url}/ping"
        r = requests.get(url, headers=self._headers(), timeout=self.timeout)
        return r.status_code == 200

    def send_document(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/documents"
        r = requests.post(url, json=payload, headers=self._headers(), timeout=self.timeout)
        if r.status_code >= 400:
            logger.error("1C error %s: %s", r.status_code, r.text)
            r.raise_for_status()
        return r.json()
