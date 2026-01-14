## Синхронизация legacy endpoints и Product/UI слоя

Ранее `POST /documents/upload` (legacy) генерировал `artifact_id`, но внутри `ArtifactService.create_artifact()` создавался **другой** UUID. В результате Product/UI API не мог открыть документ по id (`GET /api/v1/documents/{id}` → 404), а список документов (`GET /api/v1/documents`) был пустым/неконсистентным относительно реальных загрузок.

Исправление сделано минимально-инвазивно: `ArtifactService.create_artifact()` теперь принимает опциональный параметр `artifact_id` и умеет **идемпотентно** создавать/обновлять артефакт с указанным id. Legacy upload использует этот id, поэтому один и тот же документ виден и для legacy pipeline, и для Product/UI API (tenant-safe).

Дополнительно улучшен UX экспорта: `POST /api/v1/invoices/{id}/export` возвращает `file_id` и `download_url`, чтобы экспорт можно было скачать через `GET /files/{file_id}`. Тесты добавлены, чтобы закрепить поведение и не допустить регрессий.

