#!/usr/bin/env python3
"""
Скрипт для ручного запуска Kaspi recurring billing.

Использование:
    python scripts/run_kaspi_recurring.py

Требования:
    - BILLING_ENABLED=1
    - KASPI_ENABLED=1
    - PLATFORM_DB_PATH (опционально, по умолчанию platform.db)
"""

import os
import sys
from pathlib import Path

# Добавляем корневую директорию в путь
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

from dotenv import load_dotenv

# Загружаем .env файл
load_dotenv()

from cyberplat.billing_service import BillingService
from cyberplat.billing_entitlements import EntitlementService
from cyberplat.billing.infrastructure.kaspi_provider import KaspiPaymentProvider
from cyberplat.billing.infrastructure.repositories import EntitlementSubscriptionRepository
from cyberplat.billing.application.renew_subscriptions_use_case import RenewSubscriptionsUseCase


def main():
    """Запустить Kaspi recurring billing."""
    # Проверка env переменных
    billing_enabled = os.getenv("BILLING_ENABLED", "0").strip() == "1"
    kaspi_enabled = os.getenv("KASPI_ENABLED", "0").strip() == "1"
    
    if not billing_enabled:
        print("❌ Ошибка: BILLING_ENABLED=1 не установлен")
        print("   Установите BILLING_ENABLED=1 в .env файле")
        sys.exit(1)
    
    if not kaspi_enabled:
        print("❌ Ошибка: KASPI_ENABLED=1 не установлен")
        print("   Установите KASPI_ENABLED=1 в .env файле")
        sys.exit(1)
    
    # Инициализация сервисов
    db_path = os.getenv("PLATFORM_DB_PATH", "platform.db")
    print(f"📦 Инициализация сервисов (БД: {db_path})...")
    
    try:
        billing_service = BillingService(db_path=db_path)
        billing_service.ensure_schema()
        billing_service.seed_default_rates_if_empty()
        
        entitlement_service = EntitlementService(db_path=db_path)
        entitlement_service.ensure_schema()
        entitlement_service.seed_default_plans_if_empty()
        
        print("✓ Сервисы инициализированы")
    except Exception as e:
        print(f"❌ Ошибка при инициализации сервисов: {e}")
        sys.exit(1)
    
    # Создание use case
    try:
        kaspi_provider = KaspiPaymentProvider()
        subscription_repo = EntitlementSubscriptionRepository(
            entitlement_service,
            billing_service=billing_service
        )
        
        use_case = RenewSubscriptionsUseCase(
            provider_name="kaspi",
            subscription_repo=subscription_repo,
            kaspi_provider=kaspi_provider
        )
        
        print("✓ Use case создан")
    except Exception as e:
        print(f"❌ Ошибка при создании use case: {e}")
        sys.exit(1)
    
    # Выполнение recurring billing
    print("\n🔄 Запуск Kaspi recurring billing...")
    try:
        result = use_case.execute()
        
        # Вывод результата
        print("\n" + "=" * 50)
        print("Результат:")
        print("=" * 50)
        result_dict = result.to_dict()
        print(f"  Charged: {result_dict['charged']}")
        print(f"  Failed:  {result_dict['failed']}")
        print(f"  Skipped: {result_dict['skipped']}")
        
        if result_dict['errors']:
            print(f"\n  Ошибки ({len(result_dict['errors'])}):")
            for error in result_dict['errors']:
                print(f"    - {error}")
        else:
            print("  Ошибок нет ✓")
        
        print("=" * 50)
        
        # Закрытие соединений
        billing_service.close()
        entitlement_service.close()
        
        sys.exit(0 if result_dict['failed'] == 0 and len(result_dict['errors']) == 0 else 1)
        
    except Exception as e:
        print(f"\n❌ Ошибка при выполнении recurring billing: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
