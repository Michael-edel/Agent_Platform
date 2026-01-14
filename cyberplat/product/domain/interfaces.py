"""Domain interfaces (ports) for product/UI layer."""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from datetime import datetime


class ArtifactStateRepository(ABC):
    """Интерфейс для работы с состояниями артефактов (UI layer)."""
    
    @abstractmethod
    def get_state(
        self,
        tenant_id: str,
        artifact_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Получить состояние артефакта.
        
        Args:
            tenant_id: ID тенанта
            artifact_id: ID артефакта
            
        Returns:
            Словарь с состоянием или None
        """
        pass
    
    @abstractmethod
    def create_or_update_state(
        self,
        tenant_id: str,
        artifact_id: str,
        ui_status: str,
        source_artifact_id: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None
    ) -> str:
        """
        Создать или обновить состояние артефакта.
        
        Args:
            tenant_id: ID тенанта
            artifact_id: ID артефакта
            ui_status: UI статус ('pending', 'processing', 'completed', 'error', 'confirmed', 'exported')
            source_artifact_id: ID исходного артефакта (для invoice → document связи)
            error_code: Код ошибки (если есть)
            error_message: Сообщение об ошибке (если есть)
            
        Returns:
            ID состояния
        """
        pass
    
    @abstractmethod
    def update_status(
        self,
        tenant_id: str,
        artifact_id: str,
        ui_status: str,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None
    ) -> bool:
        """
        Обновить статус артефакта.
        
        Args:
            tenant_id: ID тенанта
            artifact_id: ID артефакта
            ui_status: Новый UI статус
            error_code: Код ошибки (если есть)
            error_message: Сообщение об ошибке (если есть)
            
        Returns:
            True если обновлено, False если не найдено
        """
        pass
    
    @abstractmethod
    def mark_confirmed(
        self,
        tenant_id: str,
        artifact_id: str
    ) -> bool:
        """
        Отметить артефакт как подтверждённый.
        
        Args:
            tenant_id: ID тенанта
            artifact_id: ID артефакта
            
        Returns:
            True если обновлено, False если не найдено
        """
        pass
    
    @abstractmethod
    def mark_exported(
        self,
        tenant_id: str,
        artifact_id: str,
        export_target: str
    ) -> bool:
        """
        Отметить артефакт как экспортированный.
        
        Args:
            tenant_id: ID тенанта
            artifact_id: ID артефакта
            export_target: Цель экспорта ('excel', 'json', '1c', etc.)
            
        Returns:
            True если обновлено, False если не найдено
        """
        pass
    
    @abstractmethod
    def list_by_kind_and_status(
        self,
        tenant_id: str,
        kind: str,
        ui_status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Получить список артефактов по kind и статусу.
        
        Args:
            tenant_id: ID тенанта
            kind: Тип артефакта ('document', 'invoice', 'payment')
            ui_status: Фильтр по UI статусу (опционально)
            limit: Лимит результатов
            offset: Смещение для пагинации
            
        Returns:
            Список словарей с данными артефактов и их состояний
        """
        pass


class ExportRepository(ABC):
    """Интерфейс для работы с экспортами."""
    
    @abstractmethod
    def create_export(
        self,
        tenant_id: str,
        artifact_id: str,
        export_type: str,
        export_config: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Создать запись об экспорте.
        
        Args:
            tenant_id: ID тенанта
            artifact_id: ID артефакта
            export_type: Тип экспорта ('excel', 'json', '1c', etc.)
            export_config: Конфигурация экспорта (опционально)
            
        Returns:
            ID экспорта
        """
        pass
    
    @abstractmethod
    def update_export(
        self,
        export_id: str,
        status: str,
        file_id: Optional[str] = None,
        file_path: Optional[str] = None,
        error_message: Optional[str] = None
    ) -> bool:
        """
        Обновить статус экспорта.
        
        Args:
            export_id: ID экспорта
            status: Новый статус ('completed', 'failed')
            file_id: ID файла (если экспорт завершён)
            file_path: Путь к файлу (для dev/staging)
            error_message: Сообщение об ошибке (если статус 'failed')
            
        Returns:
            True если обновлено, False если не найдено
        """
        pass
    
    @abstractmethod
    def get_export(
        self,
        tenant_id: str,
        export_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Получить экспорт по ID.
        
        Args:
            tenant_id: ID тенанта
            export_id: ID экспорта
            
        Returns:
            Словарь с данными экспорта или None
        """
        pass
    
    @abstractmethod
    def list_exports(
        self,
        tenant_id: str,
        artifact_id: Optional[str] = None,
        export_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Получить список экспортов.
        
        Args:
            tenant_id: ID тенанта
            artifact_id: Фильтр по артефакту (опционально)
            export_type: Фильтр по типу экспорта (опционально)
            limit: Лимит результатов
            offset: Смещение для пагинации
            
        Returns:
            Список словарей с данными экспортов
        """
        pass
