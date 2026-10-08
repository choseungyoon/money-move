"""매매 거래대금에서 '자기자본'을 추정하는 대출 가정.

거래대금 15억 ≠ 매수자 지역에서 빠져나간 돈 15억. 주담대만큼은 금융권 자금이다.
  자기자본 = 가격 - 대출,  대출 = BORROW_SHARE × min(LTV × 가격, 한도)

여기 있는 값은 공개된 정책 발표를 단순화한 '가정'이다. 실제 대출은 DSR, 주택 수,
생애최초 여부, 승계 전세금(갭투자)에 따라 크게 다르며 이 모형은 그것을 반영하지 않는다.
정책 파라미터는 실데이터 운영 전에 원문과 대조해 검증해야 한다.
"""

BORROW_SHARE = 0.65  # 매수자 중 주담대 이용 비율 × 한도 소진율 (가정)

TOP4 = {"11680", "11650", "11710", "11170"}  # 강남·서초·송파·용산: 기간 내내 규제지역
REG12_GG = {"41290", "41210", "41131", "41133", "41135", "41111", "41115", "41117",
            "41173", "41465", "41430", "41450"}  # 10·15 대책으로 지정된 경기 12곳

POLICY_6_27 = "2025-07"   # 2025-06-28 시행 → 월 단위로는 7월부터 반영
POLICY_10_15 = "2025-11"  # 2025-10-16 시행 → 월 단위로는 11월부터 반영


def regulated(code, ym):
    if code in TOP4:
        return True
    return ym >= POLICY_10_15 and (code.startswith("11") or code in REG12_GG)


def ltv(code, ym):
    if regulated(code, ym):
        return 0.40 if ym >= POLICY_10_15 else 0.50
    return 0.70


def loan_cap(price_eok, ym):
    """수도권 주담대 한도(억). None은 한도 없음."""
    if ym >= POLICY_10_15:
        return 6.0 if price_eok <= 15 else 4.0 if price_eok <= 25 else 2.0
    if ym >= POLICY_6_27:
        return 6.0
    return None


def equity(price_eok, code, ym):
    """거래 1건의 추정 자기자본(억)."""
    if price_eok <= 0:
        return 0.0
    loan = ltv(code, ym) * price_eok
    cap = loan_cap(price_eok, ym)
    if cap is not None:
        loan = min(loan, cap)
    return price_eok - BORROW_SHARE * loan
