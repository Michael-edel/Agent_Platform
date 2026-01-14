# Frontend Setup - CyberPlat MVP

## ✅ Проект создан

Создан production-grade MVP фронтенд для CyberPlat на Next.js 14 + TypeScript.

## Структура проекта

```
frontend/
├── app/                          # Next.js App Router
│   ├── layout.tsx               # Root layout
│   ├── page.tsx                 # Home page (redirects to /app/documents)
│   ├── globals.css              # Global styles
│   ├── settings/
│   │   └── page.tsx             # Settings page (baseUrl + tenantId)
│   ├── documents/
│   │   ├── page.tsx             # Documents list
│   │   └── [id]/
│   │       └── page.tsx        # Document detail + PDF viewer + Run OCR
│   ├── invoices/
│   │   ├── page.tsx             # Invoices list
│   │   └── [id]/
│   │       └── page.tsx        # Invoice detail + Confirm + Export
│   └── billing/
│       └── page.tsx             # Billing invoice viewer
├── components/
│   ├── Nav.tsx                  # Navigation bar
│   ├── ErrorBanner.tsx          # Error messages
│   └── SuccessBanner.tsx        # Success messages
├── lib/
│   ├── api.ts                   # API client (all endpoints)
│   └── storage.ts               # localStorage utilities
├── middleware.ts                # Next.js middleware
├── package.json                 # Dependencies
├── tsconfig.json                # TypeScript config
├── next.config.js               # Next.js config
├── .eslintrc.json               # ESLint config
├── .gitignore                   # Git ignore
└── README.md                    # Documentation
```

## Команды запуска

```bash
# 1. Перейти в папку frontend
cd frontend

# 2. Установить зависимости
npm install

# 3. Запустить dev server
npm run dev

# 4. Открыть в браузере
# http://localhost:3000
```

## Проверка типов

```bash
cd frontend
npm run build
```

Это проверит TypeScript типы и соберёт проект.

## Настройка

1. Запустите backend на `http://127.0.0.1:8000`
2. Откройте `http://localhost:3000`
3. Перейдите в Settings (`/app/settings`)
4. Введите:
   - Base URL: `http://127.0.0.1:8000`
   - Tenant ID: `tenant-1` (или любой другой)
5. Сохраните настройки

## Реализованные функции

### ✅ Settings
- Настройка baseUrl и tenantId
- Сохранение в localStorage
- Валидация URL
- Редирект на documents после сохранения

### ✅ Documents
- Список документов с фильтрацией по статусу
- Загрузка документов (multipart upload)
- Детальная страница документа
- PDF viewer (iframe)
- Кнопка Run OCR с loading state
- Редирект на invoice после успешного OCR

### ✅ Invoices
- Список инвойсов
- Детальная страница инвойса
- Отображение полей (fields)
- Кнопка Confirm (идемпотентная)
- Кнопки Export Excel/JSON
- Автоматическое скачивание файлов

### ✅ Billing
- Выбор периода (YYYY-MM)
- Отображение billing invoice
- Totals by metric
- Форматирование валюты

## API Client

Все функции в `lib/api.ts`:
- `uploadDocument(file)` - загрузка документа
- `listDocuments(params)` - список документов
- `getDocument(id)` - детали документа
- `runOcr(documentId)` - запуск OCR
- `listInvoices(params)` - список инвойсов
- `getInvoice(id)` - детали инвойса
- `confirmInvoice(id)` - подтверждение
- `exportInvoice(id, type)` - экспорт и скачивание
- `getBillingInvoice(period)` - billing invoice

Все запросы автоматически добавляют заголовок `X-Tenant-ID`.

## Компоненты

- **Nav** - навигация между страницами
- **ErrorBanner** - отображение ошибок с возможностью закрыть
- **SuccessBanner** - отображение успешных операций

## Стили

Минимальные стили в `globals.css`:
- Карточки (card)
- Кнопки (btn-primary, btn-secondary, btn-success, btn-danger)
- Таблицы (table)
- Формы (form-input, form-label)
- Бейджи статусов (badge)
- Алерты (alert)
- Loading spinner

## UX Логика

1. **Проверка tenantId**: Если не установлен → редирект на `/app/settings`
2. **Upload**: После успешной загрузки → refresh списка + success message
3. **Run OCR**: Loading state → success → редирект на invoice detail
4. **Confirm**: Обновление локального state + success message
5. **Export**: Скачивание файла + success message + обновление state

## Типы данных

Гибкие типы в `lib/api.ts`:
- `DocumentListItem` - элемент списка документов
- `DocumentDetail` - детали документа
- `InvoiceListItem` - элемент списка инвойсов
- `InvoiceDetail` - детали инвойса
- `BillingInvoice` - billing invoice

Все поля опциональны, UI показывает "—" если данных нет.

## Следующие шаги (опционально)

1. Добавить shadcn/ui для более красивого UI
2. Добавить аутентификацию (JWT tokens)
3. Добавить пагинацию для списков
4. Добавить фильтры по статусу в UI
5. Улучшить обработку ошибок (retry, toast notifications)
6. Добавить тесты (Jest + React Testing Library)

## Проверка сборки

```bash
cd frontend
npm run build
```

Если сборка проходит без ошибок - всё готово к запуску!
