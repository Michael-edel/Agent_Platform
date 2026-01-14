"""Type definitions for application layer results."""

from dataclasses import dataclass
from typing import List


@dataclass
class RecurringResult:
    """Результат выполнения recurring billing для подписок."""
    
    charged: int
    """Количество успешно списанных подписок."""
    
    failed: int
    """Количество неудачных списаний."""
    
    skipped: int
    """Количество пропущенных подписок (период еще не закончился или уже обработан)."""
    
    errors: List[str]
    """Список ошибок."""
    
    def to_dict(self) -> dict:
        """
        Сериализовать в словарь для JSON ответа.
        
        Returns:
            Словарь с ключами: charged, failed, skipped, errors
        """
        return {
            "charged": self.charged,
            "failed": self.failed,
            "skipped": self.skipped,
            "errors": self.errors
        }
