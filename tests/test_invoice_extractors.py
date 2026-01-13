"""Тесты для извлечения данных из счёта"""

import unittest
from utils.extractors import (
    extract_iban, extract_bik, extract_kbe, extract_payment_purpose,
    extract_barcodes_from_text, extract_payment_details, cleanup_sku
)

class TestInvoiceExtractors(unittest.TestCase):
    
    def test_extract_iban(self):
        text = "Сч. № KZ0396506F0007735246"
        iban = extract_iban(text)
        self.assertEqual(iban, "KZ0396506F0007735246")
    
    def test_extract_bik(self):
        text = "АО ForteBank г. Алматы, БИК IRTYKZKA"
        bik = extract_bik(text)
        self.assertEqual(bik, "IRTYKZKA")
    
    def test_extract_kbe(self):
        text = "ТОО Michael, КБе 17"
        kbe = extract_kbe(text)
        self.assertEqual(kbe, "17")
    
    def test_extract_payment_purpose(self):
        text = "Назначение платежа: Оплата по заказу клиента №ЦБ-11777"
        purpose = extract_payment_purpose(text)
        self.assertIn("ЦБ-11777", purpose)
    
    def test_extract_ean13(self):
        text = "Товар с EAN: 4690612039183"
        barcodes = extract_barcodes_from_text(text)
        self.assertIn("4690612039183", barcodes)
    
    def test_cleanup_sku(self):
        self.assertEqual(cleanup_sku("2 ЦБ-00007632"), "ЦБ-00007632")
        self.assertEqual(cleanup_sku("ЦБ-00007632"), "ЦБ-00007632")
        self.assertEqual(cleanup_sku(None), None)
    
    def test_extract_payment_details_full(self):
        text = """
        АО "ForteBank" г. Алматы, БИК IRTYKZKA
        ИИН/БИН 170940023817
        Сч. № KZ0396506F0007735246
        ТОО Michael, КБе 17
        Код ЗК251200ЦБ0117770001
        Назначение: Оплата по заказу клиента №ЦБ-11777
        """
        payment = extract_payment_details(text)
        self.assertEqual(payment.beneficiary_bank_bik, "IRTYKZKA")
        self.assertEqual(payment.beneficiary_account_iban, "KZ0396506F0007735246")
        self.assertEqual(payment.beneficiary_bin_iin, "170940023817")
        self.assertEqual(payment.kbe, "17")
        self.assertIn("ЦБ-11777", payment.payment_purpose or "")

if __name__ == "__main__":
    unittest.main()
