# Architecture Decision Records (ADR)

## What is an ADR?

ADR (Architecture Decision Record) — это документ, который фиксирует важные архитектурные решения проекта, контекст их принятия, рассмотренные альтернативы и последствия.

**Зачем ADR в проекте:**
- Сохраняют контекст решений для будущих разработчиков
- Объясняют "почему", а не только "что"
- Помогают избежать повторного обсуждения уже принятых решений
- Документируют trade-offs и альтернативы

**Формат ADR:**
- **Context**: ситуация, которая привела к решению
- **Decision**: принятое решение
- **Alternatives Considered**: рассмотренные альтернативы и причины отклонения
- **Consequences**: положительные и отрицательные последствия
- **Validation**: как решение проверяется (тесты, runtime checks)

---

## ADR List

| ID | Title | Status | Date |
|----|-------|--------|------|
| [ADR-0001](0001-psycopg-v3-database-url-normalization.md) | psycopg v3 and DATABASE_URL normalization | Accepted | 2024-12-19 |

### ADR-0001: psycopg v3 and DATABASE_URL normalization

**Проблема:** SQLAlchemy по умолчанию использует `psycopg2` для `postgresql://...` URL, но проект использует `psycopg v3`.

**Решение:** Централизованная нормализация `DATABASE_URL` для SQLAlchemy/Alembic: `postgresql://...` → `postgresql+psycopg://...`

**Ключевые последствия:**
- ✅ Production-safe, идемпотентная нормализация
- ✅ Совместимость с docker-compose, CI/CD без изменений
- ⚠️ Дополнительный слой логики требует поддержки

**Ссылка:** [`0001-psycopg-v3-database-url-normalization.md`](0001-psycopg-v3-database-url-normalization.md)

---

## How to Add a New ADR

### Шаг 1: Создать файл ADR

Создайте новый файл в формате:
```
docs/adr/000X-short-title.md
```

Где:
- `000X` — следующий порядковый номер (0002, 0003, ...)
- `short-title` — краткое описание решения (kebab-case)

### Шаг 2: Использовать шаблон

```markdown
# ADR-000X: Short Title

**Status**: Proposed / Accepted / Deprecated  
**Date**: YYYY-MM-DD  
**Deciders**: Architecture Team / [Names]

---

## Context

[Описание ситуации, которая привела к решению]

## Decision

[Принятое решение и его реализация]

## Alternatives Considered

### 1. Alternative Name
**Отклонено:**
- Причина отклонения

## Consequences

### Положительные
- ✅ Пункт 1
- ✅ Пункт 2

### Нейтральные / Отрицательные
- ⚠️ Пункт 1
- ⚠️ Пункт 2

## Validation

[Как решение проверяется: тесты, runtime checks, etc.]

## Links / References

- Ссылки на код, тесты, документацию

---

**Related ADRs**: [ADR-0001](0001-psycopg-v3-database-url-normalization.md) (если есть связи)
```

### Шаг 3: Обновить индекс

Добавьте новую запись в таблицу "ADR List" в этом файле (`docs/adr/README.md`).

### Шаг 4: Обновить связанные ADR

Если новый ADR связан с существующими, добавьте ссылку в секцию "Related ADRs".

---

## ADR Status

- **Proposed**: решение предложено, но ещё не принято
- **Accepted**: решение принято и реализовано
- **Deprecated**: решение устарело, заменено новым ADR
- **Superseded**: решение заменено другим ADR (указать ссылку на новый ADR)

---

## Best Practices

1. **Создавайте ADR для архитектурных решений**, а не для каждого изменения кода
2. **Будьте конкретны**: опишите контекст, альтернативы и последствия
3. **Обновляйте статус**: если решение изменилось, обновите статус ADR
4. **Связывайте ADR**: если решения связаны, добавьте ссылки в "Related ADRs"
5. **Валидируйте**: опишите, как решение проверяется (тесты, runtime checks)

---

**Последнее обновление**: 2024-12-19
