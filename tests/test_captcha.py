from src.filters.captcha import generate_captcha, check_answer

def test_generate():
    for lang in ["ru","en","tr","uk","hi"]:
        c = generate_captcha(lang)
        assert c["answer"] == c["a"] + c["b"]
        assert c["trap_answer"] == c["answer"] // 2

def test_check_ok():
    assert check_answer("18", 18, 9) == "ok"
    assert check_answer("  18  ", 18, 9) == "ok"
    assert check_answer("ответ 18!", 18, 9) == "ok"

def test_check_trap():
    assert check_answer("9", 18, 9) == "trap"
    assert check_answer("9 ", 18, 9) == "trap"

def test_check_wrong():
    assert check_answer("10", 18, 9) == "wrong"
    assert check_answer("abc", 18, 9) == "wrong"
    assert check_answer("", 18, 9) == "wrong"

def test_odd_answer():
    # 19 -> trap 9, не 9.5
    c = {"answer": 19, "trap": 9}
    assert check_answer("9", 19, 9) == "trap"
    assert check_answer("19", 19, 9) == "ok"
