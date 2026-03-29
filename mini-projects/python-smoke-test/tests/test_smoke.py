from python_smoke_test.greetings import greet


def test_greet() -> None:
    assert 'Hola' in greet()
