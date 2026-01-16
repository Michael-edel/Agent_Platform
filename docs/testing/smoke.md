# Smoke тест: загрузка PDF локально (без внешних сервисов)

## Запуск

```bash
docker compose up -d
```

## Загрузка PDF (PowerShell)

Подготовьте файл `invoice.pdf` (любой маленький PDF) в текущей директории и выполните:

```powershell
$tenantId = "tenant-1"

# Upload (legacy upload endpoint)
$resp = curl.exe -s -X POST "http://localhost:8000/documents/upload" `
  -H "X-Tenant-ID: $tenantId" `
  -F "file=@invoice.pdf;type=application/pdf"

$resp
```

В ответе будет `artifact_id` (ID документа).

## Где смотреть результат

```powershell
$tenantId = "tenant-1"
$docId = "<artifact_id>"

# Список документов (Product/UI API)
curl.exe -s "http://localhost:8000/api/v1/documents?limit=20" -H "X-Tenant-ID: $tenantId"

# Детали документа
curl.exe -s "http://localhost:8000/api/v1/documents/$docId" -H "X-Tenant-ID: $tenantId"
```

## Автотест

В контейнере:

```bash
docker compose exec app python -m pytest -q --tb=short tests/test_smoke_upload_invoice_pdf.py
```

