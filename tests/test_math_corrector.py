from core.math_corrector import MathCorrector
from models.document import DocumentData, DocumentItem

def test_math_correction():
    doc = DocumentData(document_type="invoice", items=[
        DocumentItem(name="Товар", quantity="2", price="10.00", total="1.00")
    ], total="1.00")
    corrected = MathCorrector().correct_document(doc)
    assert corrected.items[0].total == "20.00"
