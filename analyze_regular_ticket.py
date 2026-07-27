# -*- coding: utf-8 -*-
import csv
import re
from collections import Counter

path = r"C:\Users\Ethan\Downloads\[현장별 판매중 플랫폼 상품 내역]_제목 없는 페이지_피봇 테이블 (3).csv"

names = set()
with open(path, encoding="utf-8-sig") as f:
    reader = csv.reader(f)
    header = next(reader)
    for row in reader:
        if len(row) < 9:
            continue
        kakao_name = row[4].strip()
        modu_name = row[8].strip()
        if kakao_name:
            names.add(kakao_name)
        if modu_name:
            names.add(modu_name)

night_kw = re.compile("야간|심야")
weekday_kw = re.compile("평일")
holiday_kw = re.compile("휴일|주말")
daytime_kw = re.compile("주간")

categories = Counter()
ambiguous = []
uncategorized = []

for n in sorted(names):
    has_night = bool(night_kw.search(n))
    has_weekday = bool(weekday_kw.search(n))
    has_holiday = bool(holiday_kw.search(n))
    has_daytime = bool(daytime_kw.search(n))

    flags = sum([has_night, has_weekday, has_holiday])
    if flags >= 2:
        ambiguous.append(n)
    elif has_night:
        categories["심야"] += 1
    elif has_weekday:
        categories["평일"] += 1
    elif has_holiday:
        categories["휴일"] += 1
    elif has_daytime:
        uncategorized.append(n)  # 주간: 4개 분류에 없음
    else:
        categories["일반"] += 1

print(f"전체 고유 상품명 수: {len(names)}")
print(f"카테고리별 개수: {dict(categories)}")

print(f"\n=== 2개 이상 키워드 동시 포함(애매한 케이스) - {len(ambiguous)}개 ===")
for n in ambiguous:
    print(f"  {n}")

print(f"\n=== '주간'만 있고 4개 분류 키워드 없음 - {len(uncategorized)}개 ===")
for n in uncategorized:
    print(f"  {n}")

# 일반으로 분류된 것 중 위치/기타 설명이 붙은 케이스도 살펴보기 (전용, 기계식 등)
print("\n=== '일반' 분류인데 괄호 설명이 붙은 케이스 샘플 ===")
etc_kw = re.compile(r"\(")
count = 0
for n in sorted(names):
    has_night = bool(night_kw.search(n))
    has_weekday = bool(weekday_kw.search(n))
    has_holiday = bool(holiday_kw.search(n))
    if not (has_night or has_weekday or has_holiday) and etc_kw.search(n):
        print(f"  {n}")
        count += 1
print(f"(총 {count}개)")
