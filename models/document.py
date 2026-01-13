"""Модели данных для документов и товаров"""

from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any

@dataclass
class DocumentItem:
    name: str
    sku: Optional[str] = None
    quantity: str = "0"
    unit: str = "шт"
    price: str = "0.00"
    total: str = "0.00"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DocumentItem":
        return cls(
            name=data.get("name", ""),
            sku=data.get("sku"),
            quantity=str(data.get("quantity", 0)),
            unit=data.get("unit", "шт"),
            price=str(data.get("price", 0)),
            total=str(data.get("total", 0)),
        )

@dataclass
class Party:
    """Сторона сделки (поставщик или покупатель)"""
    name: Optional[str] = None
    bin_iin: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Party":
        return cls(
            name=data.get("name"),
            bin_iin=data.get("bin_iin") or data.get("bin") or data.get("iin"),
            address=data.get("address"),
            phone=data.get("phone"),
        )

@dataclass
class PaymentDetails:
    """Реквизиты платежа для счёта"""
    beneficiary_bank_name: Optional[str] = None
    beneficiary_bank_bik: Optional[str] = None
    beneficiary_account_iban: Optional[str] = None
    beneficiary_bin_iin: Optional[str] = None
    kbe: Optional[str] = None
    knp: Optional[str] = None
    payment_code: Optional[str] = None
    payment_purpose: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PaymentDetails":
        return cls(
            beneficiary_bank_name=data.get("beneficiary_bank_name"),
            beneficiary_bank_bik=data.get("beneficiary_bank_bik"),
            beneficiary_account_iban=data.get("beneficiary_account_iban"),
            beneficiary_bin_iin=data.get("beneficiary_bin_iin") or data.get("beneficiary_bin") or data.get("beneficiary_iin"),
            kbe=data.get("kbe"),
            knp=data.get("knp"),
            payment_code=data.get("payment_code"),
            payment_purpose=data.get("payment_purpose"),
        )

@dataclass
class DocumentData:
    document_type: str
    iin_bin: Optional[str] = None
    company_name: Optional[str] = None
    document_number: Optional[str] = None
    document_date: Optional[str] = None
    subtotal: Optional[str] = None
    vat: Optional[str] = None
    total: Optional[str] = None
    items: List[DocumentItem] = None
    error: Optional[str] = None
    page_number: Optional[int] = None
    total_pages_processed: Optional[int] = None
    # Новые поля для счёта
    supplier: Optional[Party] = None
    buyer: Optional[Party] = None
    payment: Optional[PaymentDetails] = None
    barcodes: List[str] = None

    def __post_init__(self):
        if self.items is None:
            self.items = []
        if self.barcodes is None:
            self.barcodes = []

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["items"] = [item.to_dict() for item in self.items]
        if self.supplier:
            result["supplier"] = self.supplier.to_dict()
        if self.buyer:
            result["buyer"] = self.buyer.to_dict()
        if self.payment:
            result["payment"] = self.payment.to_dict()
        result["barcodes"] = self.barcodes or []
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DocumentData":
        items_data = data.get("items", []) or []
        items = [DocumentItem.from_dict(item) for item in items_data]
        
        supplier = None
        if data.get("supplier"):
            supplier = Party.from_dict(data["supplier"])
        
        buyer = None
        if data.get("buyer"):
            buyer = Party.from_dict(data["buyer"])
        
        payment = None
        if data.get("payment"):
            payment = PaymentDetails.from_dict(data["payment"])
        
        barcodes = data.get("barcodes", []) or []
        if isinstance(barcodes, str):
            barcodes = [barcodes]
        
        return cls(
            document_type=data.get("document_type", "unknown"),
            iin_bin=data.get("iin_bin") or data.get("iin") or data.get("bin"),
            company_name=data.get("company_name"),
            document_number=data.get("document_number"),
            document_date=data.get("document_date"),
            subtotal=data.get("subtotal"),
            vat=data.get("vat"),
            total=data.get("total"),
            items=items,
            error=data.get("error"),
            page_number=data.get("page_number"),
            total_pages_processed=data.get("total_pages_processed"),
            supplier=supplier,
            buyer=buyer,
            payment=payment,
            barcodes=barcodes,
        )

@dataclass
class InventoryItem:
    id: int
    id_1c: str
    name: str
    sku: Optional[str]
    unit: str
    category: Optional[str]
    is_active: bool
    updated_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class ProductMapping:
    supplier_item_name: str
    supplier_sku: Optional[str]
    my_item_id_1c: str
    my_item_name: str
    confidence: int
    usage_count: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
