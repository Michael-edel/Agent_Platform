"""Сервис для работы с файловым хранилищем."""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class StorageService:
    """Сервис для получения путей к файлам."""
    
    def __init__(self, base_path: str = "out/jobs"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"StorageService инициализирован: {self.base_path}")
    
    def get_file_path(self, file_id: str) -> Optional[Path]:
        """
        Получить путь к файлу по file_id.
        
        Args:
            file_id: ID файла (может быть имя файла или полный путь)
            
        Returns:
            Путь к файлу или None, если файл не найден
        """
        # Если file_id уже полный путь
        if Path(file_id).is_absolute() or Path(file_id).exists():
            path = Path(file_id)
            if path.exists():
                return path
        
        # Ищем в базовой директории
        path = self.base_path / file_id
        if path.exists():
            return path
        
        # Ищем файлы, начинающиеся с file_id
        for file_path in self.base_path.glob(f"{file_id}*"):
            if file_path.is_file():
                return file_path

        # Экспорты (product layer) сейчас генерируются в out/exports.
        # Для удобства /files/{file_id} поддерживаем поиск и там (без изменения контрактов).
        exports_dir = Path("out/exports")
        if exports_dir.exists():
            export_path = exports_dir / file_id
            if export_path.exists():
                return export_path
            for file_path in exports_dir.glob(f"{file_id}*"):
                if file_path.is_file():
                    return file_path
        
        logger.warning(f"Файл не найден: {file_id}")
        return None
