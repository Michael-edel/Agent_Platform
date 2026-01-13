"""Базовый класс для агентов."""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class AgentContext:
    """Контекст выполнения агента."""
    artifact_id: str
    file_id: Optional[str] = None
    tenant_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class BaseAgent(ABC):
    """Базовый класс для всех агентов."""
    
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        logger.info(f"Инициализирован агент: {self.name}")
    
    @abstractmethod
    async def run(self, context: AgentContext) -> Dict[str, Any]:
        """
        Выполнить агента.
        
        Args:
            context: Контекст выполнения агента
            
        Returns:
            Результат выполнения агента
        """
        pass
    
    def get_info(self) -> Dict[str, Any]:
        """Получить информацию об агенте."""
        return {
            "name": self.name,
            "description": self.description
        }
