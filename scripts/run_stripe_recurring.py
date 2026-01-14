#!/usr/bin/env python3
"""
Скрипт для ручного запуска Stripe recurring billing (subscription sync).

Использование:
    python scripts/run_stripe_recurring.py

Требования:
    - BILLING_ENABLED=1
    - STRIPE_ENABLED=1
    - STRIPE_SECRET_KEY (для доступа к Stripe API)
    - PLATFORM_DB_PATH (опционально, по умолчанию platform.db)

Примечание:
    Stripe recurring НЕ инициирует новые платежи, а только синхронизирует
    периоды подписок из Stripe API в локальную БД.
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
from cyberplat.billing.infrastructure.stripe_provider import StripePaymentProvider
from cyberplat.billing.infrastructure.repositories import EntitlementSubscriptionRepository
from cyberplat.billing.application.renew_subscriptions_use_case import RenewSubscriptionsUseCase


def main():
    """Запустить Stripe recurring billing (subscription sync)."""
    # Проверка env переменных
    billing_enabled = os.getenv("BILLING_ENABLED", "0").strip() == "1"
    stripe_enabled = os.getenv("STRIPE_ENABLED", "0").strip() == "1"
    stripe_secret_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    
    if not billing_enabled:
        print("❌ Ошибка: BILLING_ENABLED=1 не установлен")
        print("   Установите BILLING_ENABLED=1 в .env файле")
        sys.exit(1)
    
    if not stripe_enabled:
        print("❌ Ошибка: STRIPE_ENABLED=1 не установлен")
        print("   Установите STRIPE_ENABLED=1 в .env файле")
        sys.exit(1)
    
    if not stripe_secret_key:
        print("❌ Ошибка: STRIPE_SECRET_KEY не установлен")
        print("   Установите STRIPE_SECRET_KEY в .env файле")
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
        stripe_provider = StripePaymentProvider()
        subscription_repo = EntitlementSubscriptionRepository(
            entitlement_service,
            billing_service=billing_service
        )
        
        use_case = RenewSubscriptionsUseCase(
            provider_name="stripe",
            subscription_repo=subscription_repo,
            stripe_provider=stripe_provider
        )
        
        print("✓ Use case создан")
    except Exception as e:
        print(f"❌ Ошибка при создании use case: {e}")
        sys.exit(1)
    
    # Выполнение recurring billing
    print("\n🔄 Запуск Stripe recurring billing (subscription sync)...")
    try:
        result = use_case.execute()
        
        # Вывод результата
        print("\n" + "=" * 50)
        print("Результат:")
        print("=" * 50)
        result_dict = result.to_dict()
        print(f"  Charged (synced): {result_dict['charged']}")
        print(f"  Failed:           {result_dict['failed']}")
        print(f"  Skipped:          {result_dict['skipped']}")
        
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
