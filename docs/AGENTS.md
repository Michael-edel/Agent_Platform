# Agent SKU

## Обзор

Agent SKU (Stock Keeping Unit) — это продуктовая единица агента в каталоге платформы.
Представляет собой описание агента как продаваемого товара, без логики выполнения.

## Назначение

- **Каталог агентов**: Централизованное хранение информации об агентах
- **Pricing**: Связь с моделью ценообразования
- **Admin**: Управление через SQLAdmin панель
- **Enablement**: Основа для подключения агентов к tenant'ам (будущий функционал)

## Модель данных

### Таблица: `agent_skus`

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | UUID | Primary key |
| `code` | string | Уникальный идентификатор (e.g., `sales_assistant`) |
| `name` | string | Отображаемое имя |
| `description` | text | Описание агента (nullable) |
| `status` | enum | `active`, `deprecated`, `disabled` |
| `pricing_model` | string | `subscription`, `usage_based` |
| `created_at` | ISO timestamp | Дата создания |
| `updated_at` | ISO timestamp | Дата обновления |

### Ограничения

- `code` уникален (unique constraint)
- `code` индексирован для быстрого поиска

## Статусы

| Статус | Описание |
|--------|----------|
| `active` | Агент доступен для подключения |
| `deprecated` | Агент устарел, не рекомендуется для новых подключений |
| `disabled` | Агент отключён, недоступен |

## Pricing Models

| Модель | Описание |
|--------|----------|
| `subscription` | Фиксированная подписка |
| `usage_based` | Оплата по использованию |

## Admin Panel

Доступ через `/admin` → "Agent SKUs":

- **Просмотр списка**: code, name, status, pricing_model, created_at
- **Создание**: Добавление нового агента
- **Редактирование**: Изменение полей
- **Удаление**: Запрещено (используйте `status=disabled`)

**Доступ**: только `platform_admin`

## Важно

⚠️ **Execution НЕ реализован**

Agent SKU — это только продуктовый каталог. На данном этапе:

- Нет API для выполнения агентов
- Нет связи с tenant'ами (TenantAgent)
- Нет интеграции с billing/limits

Эти функции будут добавлены в следующих итерациях.

## Миграция

```bash
# Применить
alembic upgrade head

# Откатить
alembic downgrade -1
```

## Примеры

### Создание агента через Admin

1. Перейти в `/admin`
2. Выбрать "Agent SKUs"
3. Нажать "Create"
4. Заполнить:
   - code: `sales_assistant`
   - name: `Sales Assistant`
   - status: `active`
   - pricing_model: `subscription`
5. Сохранить

### Отключение агента

1. Открыть агент в Admin
2. Изменить status на `disabled`
3. Сохранить
