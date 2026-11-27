from decimal import Decimal as D

from trailhead.core.sizing import WarningCode, check_buy, risk_per_trade, suggested_shares


def test_suggested_shares():
    risk = risk_per_trade(D(100000), D("0.01"))
    assert risk == 1000
    assert suggested_shares(risk, D("127.40"), D("121.00")) == 156
    assert suggested_shares(risk, D("43.18"), D("40.26")) == 340  # 342 rounded down to a multiple of 4
    assert suggested_shares(risk, D("43.18"), D("40.26"), multiple=1) == 342
    assert suggested_shares(D(300), D(200), D(100)) == 3  # fewer than 4: not rounded to 0
    assert suggested_shares(risk, D(10), D(10)) == 0


def _codes(chk):
    return {w.code for w in chk.warnings}


def test_warnings():
    chk = check_buy(price=D("127.40"), stop=D("121.00"), shares=D(156), deployed=D(85000), max_amount=D(100000),
                    risk_per_trade=D(1000))
    assert _codes(chk) == {WarningCode.EXCEEDS_MAX}  # available cash is never checked
    assert chk.risk_dollars == D("998.40")
    assert not chk.blocking


def test_risk_and_invalid_stop():
    chk = check_buy(price=D(100), stop=D(90), shares=D(106), deployed=D(0), max_amount=D(100000),
                    risk_per_trade=D(1000))
    assert _codes(chk) == {WarningCode.EXCEEDS_RISK}
    ok = check_buy(price=D(100), stop=D(90), shares=D(105), deployed=D(0), max_amount=D(100000),
                   risk_per_trade=D(1000))
    assert ok.warnings == []
    bad = check_buy(price=D(100), stop=D(100), shares=D(1), deployed=D(0), max_amount=D(100000),
                    risk_per_trade=D(1000))
    assert bad.blocking
