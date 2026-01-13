"""Реестр агентов."""

import logging
from typing import Dict, Optional
from cyberplat.base_agent import BaseAgent

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Реестр для регистрации и получения агентов."""
    
    def __init__(self):
        self._agents: Dict[str, BaseAgent] = {}
        logger.info("AgentRegistry инициализирован")
    
    def register(self, agent: BaseAgent) -> None:
        """
        Зарегистрировать агента.
        
        Args:
            agent: Экземпляр агента
        """
        self._agents[agent.name] = agent
        logger.info(f"Зарегистрирован агент: {agent.name}")
    
    def get(self, name: str) -> Optional[BaseAgent]:
        """
        Получить агента по имени.
        
        Args:
            name: Имя агента
            
        Returns:
            Агент или None, если не найден
        """
        return self._agents.get(name)
    
    def list_agents(self) -> list:
        """
        Получить список всех зарегистрированных агентов.
        
        Returns:
            Список информации об агентах
        """
        return [agent.get_info() for agent in self._agents.values()]
