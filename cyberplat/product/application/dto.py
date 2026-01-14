"""DTOs (Data Transfer Objects) for product/UI API."""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel


class ArtifactStateDTO(BaseModel):
    """DTO для состояния артефакта."""
    ui_status: str
    source_artifact_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    confirmed_at: Optional[str] = None
    exported_at: Optional[str] = None
    export_target: Optional[str] = None
    updated_at: str
    
    class Config:
        from_attributes = True


class DocumentDTO(BaseModel):
    """DTO для документа (UI projection, без raw OCR JSON)."""
    id: str
    kind: str
    source: str
    tenant_id: str
    created_at: str
    state: ArtifactStateDTO
    
    class Config:
        from_attributes = True


class InvoiceDTO(BaseModel):
    """DTO для инвойса (UI projection, без raw OCR JSON)."""
    id: str
    kind: str
    source: str
    tenant_id: str
    created_at: str
    state: ArtifactStateDTO
    # Дополнительные поля для инвойса (извлечённые из data, но не raw JSON)
    invoice_number: Optional[str] = None
    total_amount: Optional[str] = None
    supplier_name: Optional[str] = None
    date: Optional[str] = None
    
    class Config:
        from_attributes = True


class DocumentDetailDTO(BaseModel):
    """DTO для детального просмотра документа (с ограниченными данными из data)."""
    id: str
    kind: str
    source: str
    tenant_id: str
    created_at: str
    state: ArtifactStateDTO
    filename: Optional[str] = None
    file_id: Optional[str] = None


class InvoiceDetailDTO(BaseModel):
    """DTO для детального просмотра инвойса (с структурированными данными, не raw JSON)."""
    id: str
    kind: str
    source: str
    tenant_id: str
    created_at: str
    state: ArtifactStateDTO
    # Структурированные поля из OCR (не raw JSON)
    invoice_number: Optional[str] = None
    total_amount: Optional[str] = None
    supplier_name: Optional[str] = None
    supplier_bin: Optional[str] = None
    date: Optional[str] = None
    items: Optional[List[Dict[str, Any]]] = None  # Список товаров/услуг


class DocumentsListResponse(BaseModel):
    """Ответ для списка документов."""
    items: List[DocumentDTO]
    total: int
    limit: int
    offset: int


class InvoicesListResponse(BaseModel):
    """Ответ для списка инвойсов."""
    items: List[InvoiceDTO]
    total: int
    limit: int
    offset: int


class ConfirmInvoiceResponse(BaseModel):
    """Ответ для подтверждения инвойса."""
    success: bool
    artifact_id: str
    message: Optional[str] = None


class ExportInvoiceRequest(BaseModel):
    """Запрос на экспорт инвойса."""
    export_type: str  # 'excel', 'json', '1c', etc.
    export_config: Optional[Dict[str, Any]] = None


class ExportInvoiceResponse(BaseModel):
    """Ответ для экспорта инвойса."""
    success: bool
    export_id: str
    artifact_id: str
    export_type: str
    status: str  # 'pending', 'completed', 'failed'
    # file_id нужен, чтобы UI/клиент мог скачать экспорт через /files/{file_id}
    file_id: Optional[str] = None
    # Удобный относительный URL для скачивания (можно конкатенировать с baseUrl на клиенте).
    download_url: Optional[str] = None
    message: Optional[str] = None
