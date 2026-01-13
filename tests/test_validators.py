from utils.validators import NumberCleaner, IINBINValidator

def test_number_cleaner():
    assert str(NumberCleaner.clean_to_decimal("1 234,50")) == "1234.50"
    assert str(NumberCleaner.clean_to_decimal("31.000,00")) == "31000.00"

def test_iin_bin_validator_length():
    ok, err = IINBINValidator.validate("123")
    assert ok is False
    assert err
