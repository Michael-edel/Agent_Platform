import re
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict


def _norm_lines(text: str) -> List[str]:
    text = text.replace("\u00a0", " ")
    lines = [ln.strip() for ln in text.splitlines()]
    return [ln for ln in lines if ln]


def extract_bik(text: str) -> Optional[str]:
    lines = _norm_lines(text)
    for i, ln in enumerate(lines):
        if ln.upper() == "БИК" and i + 1 < len(lines):
            cand = lines[i+1].strip()
            if re.fullmatch(r"[A-Z]{8}", cand):
                return cand
    m = re.search(r"\b([A-Z]{8})\b", text)
    return m.group(1) if m else None


def extract_iban(text: str) -> Optional[str]:
    m = re.search(r"\bKZ[0-9A-Z]{18,}\b", text)
    return m.group(0) if m else None


def extract_bin_iin(text: str) -> Optional[str]:
    m = re.search(r"\b\d{12}\b", text)
    return m.group(0) if m else None


def extract_kbe(text: str) -> Optional[str]:
    lines = _norm_lines(text)
    for i, ln in enumerate(lines):
        if ln.replace(" ", "") in ("КБе", "КБЕ", "КБе/КБЕ"):
            if i+1 < len(lines):
                m = re.search(r"\b(\d{2})\b", lines[i+1])
                if m:
                    return m.group(1)
    m = re.search(r"КБе\s*(\d{2})", text)
    return m.group(1) if m else None


def extract_payment_purpose(text: str) -> Optional[str]:
    lines = _norm_lines(text)
    for i, ln in enumerate(lines):
        if "Назначение платежа" in ln:
            # обычно следующее поле
            if i+1 < len(lines):
                return lines[i+1].strip()
    # fallback: строка с "Оплата по"
    m = re.search(r"(Оплата[^\n]{5,200})", text)
    return m.group(1).strip() if m else None


def extract_payment_code(text: str) -> Optional[str]:
    # в счетах часто код начинается с ЗК / ZK
    m = re.search(r"\b[ЗZ]К\w{6,}\b", text)
    return m.group(0) if m else None


def extract_bank_name(text: str) -> Optional[str]:
    lines = _norm_lines(text)
    # после заголовка "Образец заполнения платежного поручения" обычно идет банк
    for i, ln in enumerate(lines):
        if "Образец заполнения платежного поручения" in ln and i+1 < len(lines):
            return lines[i+1]
    # fallback: строка содержащая "Bank" или "Банк" и "АО"
    for ln in lines:
        if ("Bank" in ln or "Банк" in ln) and len(ln) < 120:
            return ln
    return None


def extract_parties(text: str) -> Tuple[Optional[Dict], Optional[Dict]]:
    # грубый, но рабочий парсер по блокам "Поставщик:" / "Покупатель:"
    supplier = buyer = None

    def parse_party(block: str) -> Dict:
        bin_m = re.search(r"\b\d{12}\b", block)
        phone_m = re.search(r"(тел\.?\s*:?\s*)([^\n,;]{5,40})", block, flags=re.I)
        # адрес пытаемся взять после "Республика" или "г."
        addr_m = re.search(r"(Республика[^\n]{10,200}|г\.[^\n]{5,200})", block)
        name = block.strip().split("\n")[0].strip()
        return {
            "name": name,
            "bin_iin": bin_m.group(0) if bin_m else None,
            "address": addr_m.group(0).strip() if addr_m else None,
            "phone": phone_m.group(2).strip() if phone_m else None,
        }

    m = re.search(r"Поставщик\s*:\s*(.+?)\s*Покупатель\s*:", text, flags=re.S|re.I)
    if m:
        supplier = parse_party(m.group(1))
    m2 = re.search(r"Покупатель\s*:\s*(.+?)(\n\s*Плательщик|\n\s*Договор|\n\s*Итого|$)", text, flags=re.S|re.I)
    if m2:
        buyer = parse_party(m2.group(1))
    return supplier, buyer


def extract_barcodes_from_text(text: str) -> List[str]:
    # EAN-13 и др. цифровые штрихкоды
    nums = re.findall(r"\b\d{13}\b", text)
    # уникальные, сохраняя порядок
    seen=set()
    out=[]
    for n in nums:
        if n not in seen:
            seen.add(n); out.append(n)
    return out


def merge_invoice_fields(doc_dict: dict, page_text: str) -> dict:
    # doc_dict — страница (pages[0]) или плоский документ
    payment = doc_dict.get("payment") or {}
    # заполняем только пустые
    payment.setdefault("beneficiary_bank_name", extract_bank_name(page_text))
    payment.setdefault("beneficiary_bank_bik", extract_bik(page_text))
    payment.setdefault("beneficiary_account_iban", extract_iban(page_text))
    payment.setdefault("beneficiary_bin_iin", extract_bin_iin(page_text))
    payment.setdefault("kbe", extract_kbe(page_text))
    payment.setdefault("payment_code", extract_payment_code(page_text))
    payment.setdefault("payment_purpose", extract_payment_purpose(page_text))
    # если всё пустое — не вставляем
    if any(payment.values()):
        doc_dict["payment"] = payment

    if not doc_dict.get("supplier") or not doc_dict.get("buyer"):
        sup, buy = extract_parties(page_text)
        if sup and not doc_dict.get("supplier"):
            doc_dict["supplier"] = sup
        if buy and not doc_dict.get("buyer"):
            doc_dict["buyer"] = buy

    barcodes = doc_dict.get("barcodes") or []
    if isinstance(barcodes, str):
        barcodes=[barcodes]
    bc = extract_barcodes_from_text(page_text)
    merged=[]
    seen=set()
    for v in (barcodes + bc):
        if v and v not in seen:
            seen.add(v); merged.append(v)
    if merged:
        doc_dict["barcodes"]=merged

    return doc_dict
from models.document import PaymentDetails

def cleanup_sku(value: str | None) -> str | None:
    """Normalize SKU/article.
    Example: ' 2  ЦБ-00007632 ' -> 'ЦБ-00007632'
    """
    if value is None:
        return None
    s = str(value).strip().replace("\u00A0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^\d+\s+", "", s)   # убрать '1 ' / '2 ' в начале
    s = s.replace(" ", "")
    s = re.sub(r"[^0-9A-Za-zА-Яа-я_\-\.\/]", "", s)
    return s or None

def extract_payment_details(text: str) -> PaymentDetails:
    """Extract payment details from free-form invoice text."""
    return PaymentDetails(
        beneficiary_bank_name=extract_bank_name(text),
        beneficiary_bank_bik=extract_bik(text),
        beneficiary_account_iban=extract_iban(text),
        beneficiary_bin_iin=extract_bin_iin(text),
        kbe=extract_kbe(text),
        payment_code=extract_payment_code(text),
        payment_purpose=extract_payment_purpose(text),
    )
