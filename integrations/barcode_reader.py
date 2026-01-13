from typing import List

def decode_barcodes_from_pil(pil_image) -> List[str]:
    """Пытается прочитать штрихкоды (EAN/QR/Code128) с изображения страницы.
    Требует зависимость zxing-cpp. Если ее нет — возвращает [].
    """
    try:
        import zxingcpp
    except Exception:
        return []

    try:
        # zxingcpp умеет работать с Pillow Image напрямую в новых версиях
        results = zxingcpp.read_barcodes(pil_image)
    except Exception:
        # fallback: попробуем в bytes через rgb
        try:
            import numpy as np  # обычно нет в requirements
            arr = np.array(pil_image.convert("RGB"))
            results = zxingcpp.read_barcodes(arr)
        except Exception:
            return []

    out = []
    for r in results or []:
        try:
            txt = (r.text or "").strip()
        except Exception:
            txt = ""
        if txt:
            out.append(txt)
    # uniq preserve order
    seen=set()
    uniq=[]
    for x in out:
        if x not in seen:
            seen.add(x); uniq.append(x)
    return uniq
