import base64
import calendar
import html
import io
import math
import os
import re
from datetime import date

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from google.cloud import bigquery
from google.oauth2 import service_account
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

st.set_page_config(page_title="현장별 플랫폼 상품 비교", layout="wide")

PROJECT = "kmp-platform-database"


def _logo_data_uri(filename, mime):
    path = os.path.join(os.path.dirname(__file__), "assets", filename)
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


KAKAO_LOGO_URI = _logo_data_uri("kakao_logo.svg", "image/svg+xml")
MODU_LOGO_URI = _logo_data_uri("modu_logo.png", "image/png")


@st.cache_resource
def get_bigquery_client():
    # Streamlit Cloud 등 배포 환경에서는 .streamlit/secrets.toml(로컬) 또는
    # 앱 설정의 Secrets(클라우드)에 등록된 gcp_service_account를 사용한다.
    # 로컬에서 secrets.toml이 없는 경우에만 credentials 폴더의 키 파일로 대체한다.
    if "gcp_service_account" in st.secrets:
        credentials = service_account.Credentials.from_service_account_info(
            st.secrets["gcp_service_account"]
        )
        return bigquery.Client(credentials=credentials, project=PROJECT)

    os.environ.setdefault(
        "GOOGLE_APPLICATION_CREDENTIALS",
        os.path.join(os.path.dirname(__file__), "credentials", "kmp-platform-database-85d721abe80f.json"),
    )
    return bigquery.Client(project=PROJECT)


# 정기권, 종일권, 야간권, 심야권, 연박권, N시간권, 미분류 순서로 고정 노출한다.
# 상품이 없는 (현장, ticket_type) 조합도 이 틀 자체는 항상 유지되어야 한다.
TICKET_TYPE_ORDER = ["정기권", "종일권", "야간권", "심야권", "연박권", "N시간권", "미분류"]
TICKET_TYPE_RANK = {t: i for i, t in enumerate(TICKET_TYPE_ORDER)}


def render_html(content: str):
    # 들여쓰기가 있는 멀티라인 HTML을 그대로 st.markdown에 넘기면 마크다운이 4칸 들여쓰기를
    # 코드블록으로 인식해 태그가 그대로 텍스트로 노출되는 문제가 있어 한 줄로 합쳐서 렌더링한다.
    st.markdown(re.sub(r"\n\s*", "", content), unsafe_allow_html=True)


# 정기권/종일권/도넛/표/현장정보 5개 섹션은 모두 동등한 비중의 카테고리라
# 아이콘 + 굵은 글씨 + 하단 구분선으로 통일된 제목 스타일을 쓴다.
CATEGORY_ICONS = {
    "정기권 재고 현황": "🎫",
    "종일권 재고 현황": "☀️",
    "상품종류별 플랫폼 재고 비중": "📊",
    "플랫폼별 상품 비교": "📋",
    "현장 기본 정보": "🏢",
}


def category_title(text: str, top_gap: str = "0") -> str:
    icon = CATEGORY_ICONS.get(text, "")
    return (
        f'<div style="margin:{top_gap} 0 13px;padding-bottom:6px;'
        f'border-bottom:1px solid var(--border-strong);display:flex;align-items:center;gap:6px">'
        f'<span style="font-size:16px;line-height:1">{icon}</span>'
        f'<span style="font-size:15px;font-weight:700;color:var(--text-primary);letter-spacing:0.01em">{text}</span>'
        f"</div>"
    )


@st.cache_data(ttl=86400)  # 원본 테이블은 하루 1회(UTC 01:00경) 갱신되므로 굳이 자주 재조회할 필요가 없다.
def load_site_data():
    client = get_bigquery_client()
    query = f"""
        SELECT pjt_code, site_name, operation_hq, operation_manager,
               contract_type, spaces_count, open_date, close_date,
               kakao_site_id, modu_site_id
        FROM `{PROJECT}.db_platform_kmp.tb_site_data_kmp_recent`
    """
    return client.query(query).to_dataframe()


def _rank_products(products):
    # 동일 (현장, ticket_type) 안에서 product_name 내림차순 -> price 내림차순으로 정렬한 뒤
    # 순번을 매겨 카카오T/모두의주차장 상품을 같은 줄에 나란히 비교할 수 있게 짝짓는다.
    ranked = products.sort_values(["product_name", "price"], ascending=[False, False]).copy()
    ranked["rank"] = ranked.groupby(["pjt_code", "ticket_type"]).cumcount()
    return ranked


def _pair_products(kakao, modu, site_attrs):
    # kakao/modu 각각의 (현장, ticket_type)별 순번을 다시 매겨서 짝짓는다. "활성 상품만 보기"에서
    # 비활성 상품을 아예 걸러내고 이 함수를 다시 돌리면, 남은 상품들끼리 순번이 당겨져서
    # 짝이 맞지 않아 한쪽만 비는 행이 생기지 않는다.
    kakao_r = _rank_products(kakao)[
        ["pjt_code", "ticket_type", "rank", "product_id", "product_name", "price", "stock", "sales_days", "is_active"]
    ].rename(columns={
        "product_id": "kakao_product_id", "product_name": "kakao_product_name", "price": "kakao_price",
        "stock": "kakao_stock", "sales_days": "kakao_sales_days", "is_active": "kakao_is_active",
    })
    modu_r = _rank_products(modu)[
        ["pjt_code", "ticket_type", "rank", "product_id", "product_name", "price", "stock", "sales_days", "is_active"]
    ].rename(columns={
        "product_id": "modu_product_id", "product_name": "modu_product_name", "price": "modu_price",
        "stock": "modu_stock", "sales_days": "modu_sales_days", "is_active": "modu_is_active",
    })

    paired = pd.merge(kakao_r, modu_r, on=["pjt_code", "ticket_type", "rank"], how="outer")

    # pjt_code 는 중복 제거 없이 그대로 "존재하는 현장 목록"의 기준이 된다.
    # 상품이 있건 없건 모든 (현장, ticket_type) 조합이 노출되도록 스캐폴드를 만든다.
    ticket_type_df = pd.DataFrame({"ticket_type": TICKET_TYPE_ORDER})
    scaffold = site_attrs[["pjt_code"]].merge(ticket_type_df, how="cross")
    scaffold["rank"] = 0

    rank0 = scaffold.merge(paired, on=["pjt_code", "ticket_type", "rank"], how="left")
    extra = paired[paired["rank"] > 0]
    combined = pd.concat([rank0, extra], ignore_index=True)

    result = combined.merge(site_attrs, on="pjt_code", how="left")
    result["ticket_type_rank"] = result["ticket_type"].map(TICKET_TYPE_RANK)
    result = result.sort_values(["pjt_code", "ticket_type_rank", "rank"]).reset_index(drop=True)
    return result


@st.cache_data(ttl=86400)  # 원본 테이블은 하루 1회(UTC 01:00경) 갱신되므로 굳이 자주 재조회할 필요가 없다.
def load_product_data():
    client = get_bigquery_client()
    query = f"""
        SELECT project_code AS pjt_code, site_name, operation_hq, operation_manager,
               contract_type, spaces_count, ticket_type, platform, product_id,
               product_name, price, stock, sales_days, is_active
        FROM `{PROJECT}.db_platform_kmp.tb_integrated_product_data`
    """
    long_df = client.query(query).to_dataframe()
    long_df["ticket_type"] = long_df["ticket_type"].where(
        long_df["ticket_type"].isin(TICKET_TYPE_ORDER), "미분류"
    )

    kakao = long_df[long_df["platform"] == "KAKAO"].copy()
    modu = long_df[long_df["platform"] == "MODU"].copy()

    site_attrs = long_df.groupby("pjt_code", as_index=False).agg({
        "site_name": "first", "operation_hq": "first", "operation_manager": "first",
        "contract_type": "first", "spaces_count": "first",
    })

    result = _pair_products(kakao, modu, site_attrs)
    return result, kakao, modu, site_attrs


@st.cache_data(ttl=3600)  # 기간을 자주 바꿔가며 조회하므로 상품/현장 데이터보다 짧은 TTL을 쓴다.
def load_revenue_data(start_date, end_date):
    client = get_bigquery_client()
    query = f"""
        SELECT project_code AS pjt_code, platform, product_id,
               SUM(payment_count) AS revenue_count, SUM(payment_amount) AS revenue_amount
        FROM `{PROJECT}.db_platform_kmp.tb_product_revenue`
        WHERE revenue_date BETWEEN @start_date AND @end_date
        GROUP BY pjt_code, platform, product_id
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    ])
    return client.query(query, job_config=job_config).to_dataframe()


df, kakao_products, modu_products, site_attrs_all = load_product_data()
site_df = load_site_data()

render_html(
    """
    <style>
    @import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css');
    @import url('https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.45.0/dist/tabler-icons.min.css');
    html, body, [class*="css"] { font-family: 'Pretendard', -apple-system, 'Malgun Gothic', sans-serif; }
    :root {
        --surface-1: #1b1e24;
        --radius: 10px;
        --border: rgba(255,255,255,0.08);
        --border-strong: rgba(255,255,255,0.16);
        --text-primary: #ffffff;
        --text-secondary: #9aa0a6;
        --text-muted: #6b7280;
        --text-accent: #5B9BD5;
        --text-danger: #E06666;
        --map-h: 18cm;
        --map-w: 10cm;
        --stat-value-size: 24px;
    }
    </style>
    """
)

st.sidebar.header("필터")

hq_options = sorted(df["operation_hq"].dropna().unique())
hq_selected = st.sidebar.multiselect("운영본부", hq_options)

df["site_label"] = df["site_name"] + " (" + df["pjt_code"] + ")"
site_options = sorted(df["site_label"].dropna().unique())
site_choices = ["전체"] + site_options
# "전체"는 데이터량이 많아 표 렌더링이 느리므로, 첫 로딩이 빠르도록 특정 현장을 기본값으로 잡는다.
DEFAULT_SITE_LABEL = "원센티널 (KMP9900198)"
default_site_index = site_choices.index(DEFAULT_SITE_LABEL) if DEFAULT_SITE_LABEL in site_choices else 0
site_selected = st.sidebar.selectbox("현장명", site_choices, index=default_site_index)

ticket_options = sorted(df["ticket_type"].dropna().unique())
ticket_selected = st.sidebar.multiselect("상품종류", ticket_options)

st.sidebar.markdown("---")
# 취소 버튼이 눌린 다음 리런에서, 체크박스 위젯이 생성되기 "전에" 값을 되돌려야 한다
# (생성된 뒤에 같은 key의 session_state를 바꾸면 StreamlitAPIException이 발생함).
if st.session_state.pop("_reset_revenue_period", False):
    st.session_state["show_revenue_checkbox"] = False
show_revenue = st.sidebar.checkbox("기간별 매출/건수 보기", key="show_revenue_checkbox")
revenue_range = None
if show_revenue:
    _today = date.today()
    # 카카오 정기권 매출은 "정기권_NEXT"로 최대 익월 1일자까지 미리 귀속되므로, 조회 상한도
    # 오늘이 아니라 익월 말일까지 열어둬야 그 매출을 조회 기간에 포함시킬 수 있다.
    _next_month = _today.month % 12 + 1
    _next_month_year = _today.year + (_today.month // 12)
    _max_revenue_date = date(_next_month_year, _next_month, calendar.monthrange(_next_month_year, _next_month)[1])
    revenue_range = st.sidebar.date_input(
        "매출 조회 기간", value=(_today.replace(day=1), _today), max_value=_max_revenue_date
    )
    if st.sidebar.button("기간 선택 취소"):
        st.session_state["_reset_revenue_period"] = True
        st.rerun()
show_revenue_cols = show_revenue and isinstance(revenue_range, tuple) and len(revenue_range) == 2

active_only = st.sidebar.checkbox("활성(판매중) 상품만 보기", value=True)

base_filtered = df.copy()
if hq_selected:
    base_filtered = base_filtered[base_filtered["operation_hq"].isin(hq_selected)]
if site_selected != "전체":
    base_filtered = base_filtered[base_filtered["site_label"] == site_selected]

filtered = base_filtered.copy()
if ticket_selected:
    filtered = filtered[filtered["ticket_type"].isin(ticket_selected)]

# 정기권/종일권 재고 현황, 상품종류별 재고 비중 3개 요약 섹션은 "활성 상품만 보기" 체크박스와
# 무관하게 항상 활성(카카오)/판매중(모두) 상품만 집계한다 — 현재 시점 스냅샷으로 보기 위함.
summary_base = base_filtered.copy()
summary_base.loc[~summary_base["kakao_is_active"].fillna(False), ["kakao_product_name", "kakao_stock", "kakao_sales_days"]] = pd.NA
summary_base.loc[~summary_base["modu_is_active"].fillna(False), ["modu_product_name", "modu_stock", "modu_sales_days"]] = pd.NA
summary_filtered = summary_base.copy()
if ticket_selected:
    summary_filtered = summary_filtered[summary_filtered["ticket_type"].isin(ticket_selected)]

show_site_col = site_selected == "전체"
header_subtitle = (
    "전체 현장 · 카카오T vs 모두의주차장"
    if show_site_col
    else f"{site_selected.split(' (')[0]} · 카카오T vs 모두의주차장"
)
render_html(
    f"""
    <div style="margin-bottom:12px;">
        <div style="font-size:clamp(18px,2vw,22px);font-weight:500;color:var(--text-primary);">현장별 플랫폼 상품 비교 대시보드</div>
        <div style="font-size:13px;color:var(--text-secondary);margin-top:2px;">{header_subtitle}</div>
    </div>
    """
)


def classify_regular_product(name):
    if not name:
        return set()
    has_night = bool(re.search("야간|심야|야건", name))
    has_weekday = bool(re.search("평일", name))
    has_holiday = bool(re.search("휴일|주말", name))

    if has_night and has_holiday and has_weekday:
        return {"야간", "휴일"}
    if has_night and has_holiday:
        return {"휴일"}
    if has_weekday and has_night:
        return {"야간"}
    if has_weekday and has_holiday:
        return {"일반"}
    if has_night:
        return {"야간"}
    if has_weekday:
        return {"평일"}
    if has_holiday:
        return {"휴일"}
    return {"일반"}


def regular_stock_by_category(row_df):
    totals = {"일반": 0.0, "야간": 0.0, "평일": 0.0, "휴일": 0.0}
    sub = row_df[row_df["ticket_type"] == "정기권"]
    for _, r in sub.iterrows():
        for name_col, stock_col in [("kakao_product_name", "kakao_stock"), ("modu_product_name", "modu_stock")]:
            name = r[name_col]
            stock = r[stock_col]
            if pd.isna(name) or pd.isna(stock):
                continue
            for c in classify_regular_product(name):
                totals[c] += stock
    return totals


def allday_stock_by_day(row_df, day_char):
    is_allday = row_df["ticket_type"] == "종일권"
    kakao_ok = is_allday & row_df["kakao_sales_days"].fillna("").str.contains(day_char, regex=False)
    modu_ok = is_allday & row_df["modu_sales_days"].fillna("").str.contains(day_char, regex=False)
    kakao_val = row_df["kakao_stock"].where(kakao_ok, 0).fillna(0)
    modu_val = row_df["modu_stock"].where(modu_ok, 0).fillna(0)
    return (kakao_val + modu_val).sum()


def modu_map_panel_html(site_selected, site_df) -> str:
    placeholder = (
        '<div class="map-panel"><div class="map-placeholder">{msg}</div></div>'
    )
    if site_selected == "전체":
        return placeholder.format(msg="현장을 선택하면<br/>모두의주차장 지도가<br/>표시됩니다.")

    pjt_code = site_selected.split("(")[-1].rstrip(")")
    site_info = site_df[site_df["pjt_code"] == pjt_code]
    # 하나의 pjt_code에 site 행이 여러 개 붙어있을 수 있고(예: 미사용 카카오 연동이 남아있는 행),
    # 그 중 일부만 modu_site_id를 갖고 있을 수 있다. 행 순서는 BigQuery가 보장해주지 않으므로
    # 단순히 첫 행(iloc[0])을 쓰면 값이 있는데도 없는 것처럼 보일 수 있어, 값이 있는 행을 우선한다.
    valid_ids = site_info["modu_site_id"].dropna()
    valid_ids = valid_ids[valid_ids.astype(str).str.strip() != ""]
    modu_site_id = valid_ids.iloc[0] if not valid_ids.empty else None
    if site_info.empty or pd.isna(modu_site_id) or not str(modu_site_id).strip():
        return placeholder.format(msg="이 현장은 모두의주차장<br/>연동 ID가 없어 지도를<br/>표시할 수 없습니다.")

    modu_map_url = f"https://app.modu.kr/map?type=P&id={modu_site_id}#sheet=1&event=0"
    # st.iframe은 서버에서 고정 px 높이를 미리 예약해버려 뷰포트 크기에 반응할 수 없다.
    # 원시 <iframe>을 CSS로 감싸면 지도 패널은 항상 --map-h(고정 20cm)로 유지된다.
    return f'<div class="map-panel"><iframe src="{modu_map_url}"></iframe></div>'


render_html(
    """
    <style>
    .map-panel {
        background: var(--surface-1); border-radius: var(--radius); overflow:hidden;
        width: var(--map-w); height: var(--map-h); flex: none; margin: 0 auto;
    }
    /* 모두의주차장 임베드는 배율 파라미터가 없어서, iframe을 142.857%(=100/0.7)로 키운 뒤
       70%로 축소해 기본 대비 더 넓은 영역이 보이도록 한다. */
    .map-panel iframe {
        width:142.857%; height:142.857%; border:0; display:block;
        transform: scale(0.7); transform-origin: top left;
    }
    .map-placeholder {
        height:100%; box-sizing:border-box; padding:14px 17px; display:flex;
        align-items:center; justify-content:center; text-align:center;
        color: var(--text-secondary); font-size:13px; line-height:1.6;
    }
    /* 정기권 재고 카드: 컨테이너 쿼리로 카드 자체의 가로(cqw)뿐 아니라 세로(cqh)도 함께
       기준 삼아, 카드가 폭에 비해 낮아지는 경우(비율 조정 등)에도 글씨가 카드 높이를
       넘어 아래 섹션과 겹치지 않도록 두 기준 중 더 작은 쪽으로 크기를 제한한다. */
    .regular-stat-card {
        container-type: size;
        background:var(--surface-1); border-radius:var(--radius); padding:10px 12px;
        height:100%; box-sizing:border-box; overflow:hidden;
        display:flex; flex-direction:column; align-items:center; justify-content:center; gap:4px;
    }
    .regular-stat-head { display:flex; align-items:center; gap:6px; }
    .regular-stat-head i { font-size:clamp(14px, min(16cqw, 20cqh), 22px); color:var(--text-secondary); }
    /* Streamlit이 markdown 문단(p)에 자체 font-size 규칙을 걸어두고 있어, 단일 클래스
       선택자(specificity가 낮음)로는 밀리는 경우가 있다. 부모 클래스를 덧붙여
       specificity를 올려서 항상 우리 값이 이기도록 한다. */
    .regular-stat-head .regular-stat-head-label { font-size:clamp(11px, min(12cqw, 15cqh), 14px); color:var(--text-muted); margin:0; white-space:nowrap; }
    /* 정기권 카드 수치와 종일권 평일/휴일 평균 수치는 같은 크기로 보여야 해서, 서로 다른
       컨테이너(카드 크기)를 기준으로 한 상대 단위 대신 고정 크기를 공유해서 쓴다.
       --map-h가 뷰포트와 무관한 고정 물리 길이(cm)라 이 값도 고정해도 반응형이 깨지지
       않고, 더 작은 정기권 카드 쪽 높이에 맞춰뒀기 때문에 카드를 벗어나지 않는다. */
    .regular-stat-card .regular-stat-value { font-size:var(--stat-value-size); font-weight:600; margin:0; color:var(--text-primary); line-height:1; }
    /* 도넛 카드: 컨테이너 쿼리(가로 cqw/세로 cqh)로 카드 자체 크기를 기준 삼아 도넛
       지름을 정하므로, 그리드가 카드를 키우면(비중 섹션 높이를 늘리면) 차트도 함께 커진다. */
    .donut-card {
        container-type: size;
        background:var(--surface-1); border-radius:var(--radius); padding:6px 5px; text-align:center;
        height:100%; box-sizing:border-box; display:flex; flex-direction:column; justify-content:center; overflow:hidden;
    }
    .donut-circle { width:min(78cqw, 62cqh); height:min(78cqw, 62cqh); border-radius:50%; margin:0 auto 5px; position:relative; flex:none; }
    .donut-card .donut-card-title { font-size:clamp(10px, 13cqh, 15px); color:var(--text-secondary); margin:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .donut-card .donut-card-sub { font-size:clamp(8px, 9cqh, 11px); color:var(--text-muted); margin:2px 0 0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    /* 종일권 평일/휴일 평균 박스: 컨테이너 쿼리(세로 cqh 기준)로 박스 높이가 늘어나면
       숫자도 함께 커지게 한다. 가로(cqw)는 두 박스 폭이 5:2로 서로 달라 기준으로 쓰면
       두 박스 글씨 크기가 어긋나 보이므로, 높이가 서로 같은 cqh를 기준으로 통일한다. */
    .allday-box { container-type: size; background:var(--surface-1); border-radius:var(--radius); overflow:hidden; display:flex; flex-direction:column; }
    .allday-box .allday-avg-label { font-size:clamp(12px, 9cqh, 16px); color:var(--text-muted); margin:0 0 3px; }
    .allday-box .allday-avg-value { font-size:var(--stat-value-size); font-weight:600; margin:0; color:var(--text-primary); }
    .allday-box .allday-day-label { font-size:clamp(10px, 7cqh, 14px); margin:0 0 4px; }
    .allday-box .allday-day-value { font-size:clamp(16px, 15cqh, 28px); font-weight:500; margin:0; color:var(--text-primary); }
    </style>
    """
)

# 지도가 어떤 화면에서든 12cm x 20cm로 고정되므로, 같은 행에 놓이는 B구역 나머지
# 요소들(정기권/종일권/도넛)도 뷰포트 비례(vh/vw) 대신 고정 px로 맞춰 지도 높이 안에
# 항상 들어맞도록 한다.
regular_totals = regular_stock_by_category(summary_base)
day_stock = {d: allday_stock_by_day(summary_base, d) for d in ["월", "화", "수", "목", "금", "토", "일"]}
weekday_avg = round((day_stock["월"] + day_stock["화"] + day_stock["수"] + day_stock["목"] + day_stock["금"]) / 5)
weekend_avg = round((day_stock["토"] + day_stock["일"]) / 2)

WEEKEND_COLOR = {"토": "var(--text-accent)", "일": "var(--text-danger)"}
REGULAR_ICON_CLASS = {"일반": "ti-ticket", "야간": "ti-moon", "평일": "ti-briefcase", "휴일": "ti-sun"}

weekday_days_html = "".join(
    f"""
    <div style="text-align:center">
        <p class="allday-day-label" style="color:var(--text-muted)">{d}</p>
        <p class="allday-day-value">{day_stock[d]:,.0f}</p>
    </div>
    """
    for d in ["월", "화", "수", "목", "금"]
)
weekend_days_html = "".join(
    f"""
    <div style="text-align:center">
        <p class="allday-day-label" style="color:{WEEKEND_COLOR[d]}">{d}</p>
        <p class="allday-day-value" style="color:{WEEKEND_COLOR[d]}">{day_stock[d]:,.0f}</p>
    </div>
    """
    for d in ["토", "일"]
)

regular_items_html = "".join(
    f"""
    <div class="regular-stat-card">
        <div class="regular-stat-head">
            <i class="ti {REGULAR_ICON_CLASS[label]}" aria-hidden="true"></i>
            <p class="regular-stat-head-label">{label}</p>
        </div>
        <p class="regular-stat-value">{regular_totals[label]:,.0f}</p>
    </div>
    """
    for label in ["일반", "야간", "평일", "휴일"]
)

# --- 상품종류별 플랫폼 재고 비중(도넛): B구역에서 정기권/종일권과 한 열에 배치해야 해서
# 표(C구역)보다 먼저 계산한다 ---
PLATFORM_SLICE_COLOR = {"카카오T": "#F5C518", "모두의주차장": "#378ADD"}


def ticket_type_sort_key(tt):
    return (TICKET_TYPE_ORDER.index(tt) if tt in TICKET_TYPE_ORDER else len(TICKET_TYPE_ORDER), tt)


ticket_types = sorted(summary_filtered["ticket_type"].dropna().unique(), key=ticket_type_sort_key)
plat_totals = []
for tt in ticket_types:
    sub = summary_filtered[summary_filtered["ticket_type"] == tt]
    kakao_sum = sub["kakao_stock"].fillna(0).sum()
    modu_sum = sub["modu_stock"].fillna(0).sum()
    if kakao_sum + modu_sum > 0:
        plat_totals.append((tt, kakao_sum, modu_sum))

DONUT_HOLE_RATIO = 0.62  # 도넛 구멍 지름 / 바깥 지름 (기존 36px/58px 비율 유지)
DONUT_MID_R_RATIO = (0.5 + DONUT_HOLE_RATIO / 2) / 2  # 라벨을 안쪽 테두리와 바깥 테두리 중간에 놓기 위한, 지름 대비 반지름 비율


def _donut_pct_label(pct_value, mid_angle_deg, color):
    # mid_angle_deg: 12시 방향(0도)에서 시계방향으로 잰 슬라이스 중앙 각도.
    # 차트 크기가 카드마다(컨테이너 쿼리로) 달라지므로, 위치는 px가 아니라 도넛 지름 대비
    # 비율(%)로 계산해 어떤 크기에서도 항상 같은 상대 위치(안쪽/바깥 테두리 중간)에 오도록 한다.
    # transform은 위치 이동(translate)에만 쓰고 회전(rotate)은 걸지 않아 글자가 항상 수평을 유지한다.
    theta = math.radians(mid_angle_deg)
    x_pct = DONUT_MID_R_RATIO * math.sin(theta) * 100
    y_pct = -DONUT_MID_R_RATIO * math.cos(theta) * 100
    return (
        f'<span style="position:absolute;left:calc(50% + {x_pct:.2f}%);top:calc(50% + {y_pct:.2f}%);'
        f'transform:translate(-50%,-50%);font-size:clamp(8px,11cqw,11px);font-weight:600;color:{color};'
        f'white-space:nowrap;">{pct_value:.0f}%</span>'
    )


donut_cards_html = ""
for tt, kakao_sum, modu_sum in plat_totals:
    total = kakao_sum + modu_sum
    kakao_pct = (kakao_sum / total * 100) if total else 0
    modu_pct = 100 - kakao_pct

    pct_labels_html = ""
    if kakao_sum > 0:
        pct_labels_html += _donut_pct_label(kakao_pct, kakao_pct / 2 / 100 * 360, "#1b1e24")
    if modu_sum > 0:
        pct_labels_html += _donut_pct_label(modu_pct, (kakao_pct + 100) / 2 / 100 * 360, "#ffffff")

    hole_offset_pct = (100 - DONUT_HOLE_RATIO * 100) / 2
    donut_cards_html += f"""
    <div class="donut-card">
        <div class="donut-circle">
            <div style="width:100%;height:100%;border-radius:50%;
                        background:conic-gradient({PLATFORM_SLICE_COLOR['카카오T']} 0% {kakao_pct:.4f}%,{PLATFORM_SLICE_COLOR['모두의주차장']} {kakao_pct:.4f}% 100%)"></div>
            <div style="position:absolute;top:{hole_offset_pct:.2f}%;left:{hole_offset_pct:.2f}%;width:{DONUT_HOLE_RATIO*100:.2f}%;height:{DONUT_HOLE_RATIO*100:.2f}%;border-radius:50%;background:var(--surface-1)"></div>
            {pct_labels_html}
        </div>
        <p class="donut-card-title">{tt}</p>
        <p class="donut-card-sub">{kakao_sum:,.0f}건 · {modu_sum:,.0f}건</p>
    </div>
    """

donut_legend_html = f"""
<div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;font-size:12px;color:var(--text-secondary)">
    <span><span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:{PLATFORM_SLICE_COLOR['카카오T']};margin-right:4px"></span>카카오T</span>
    <span><span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:{PLATFORM_SLICE_COLOR['모두의주차장']};margin-right:4px"></span>모두의주차장</span>
</div>
"""
donut_grid_inner = (
    donut_cards_html
    if plat_totals
    else '<p style="color:var(--text-muted);font-size:12px;grid-column:1/-1;">표시할 재고 데이터가 없습니다.</p>'
)

b_left_html = f"""
<div style="height:var(--map-h);display:flex;flex-direction:column;gap:12px">
    <div style="flex:2;min-height:0;display:flex;flex-direction:column">
        {category_title("정기권 재고 현황")}
        <div style="flex:1;min-height:0;display:grid;grid-auto-rows:1fr;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:8px">{regular_items_html}</div>
    </div>
    <div style="flex:3;min-height:0;display:flex;flex-direction:column">
        {category_title("종일권 재고 현황")}
        <div style="flex:1;min-height:0;display:flex;gap:8px">
            <div class="allday-box" style="flex:5">
                <div style="padding:10px 12px;text-align:center;border-bottom:0.5px solid var(--border)">
                    <p class="allday-avg-label">평일 평균 (월~금)</p>
                    <p class="allday-avg-value">{weekday_avg:,.0f}</p>
                </div>
                <div style="flex:1;min-height:0;display:grid;grid-template-columns:repeat(5,1fr);align-content:center;padding:8px 12px 20px">{weekday_days_html}</div>
            </div>
            <div class="allday-box" style="flex:2">
                <div style="padding:10px 12px;text-align:center;border-bottom:0.5px solid var(--border)">
                    <p class="allday-avg-label">휴일 평균 (토~일)</p>
                    <p class="allday-avg-value">{weekend_avg:,.0f}</p>
                </div>
                <div style="flex:1;min-height:0;display:grid;grid-template-columns:repeat(2,1fr);align-content:center;padding:8px 12px 20px">{weekend_days_html}</div>
            </div>
        </div>
    </div>
    <div style="flex:5;min-height:0;display:flex;flex-direction:column">
        {category_title("상품종류별 플랫폼 재고 비중")}
        {donut_legend_html}
        <div style="flex:1;min-height:0;display:grid;grid-auto-rows:1fr;grid-template-columns:repeat(auto-fit,minmax(70px,1fr));gap:6px;overflow:hidden">{donut_grid_inner}</div>
    </div>
</div>
"""

map_html = modu_map_panel_html(site_selected, site_df)
render_html(
    f'<div style="display:flex;gap:16px;align-items:flex-start">'
    f'<div style="flex:1;min-width:0">{b_left_html}</div>'
    f"{map_html}"
    f"</div>"
)

# 모두의주차장 지도(iframe) 안에서 드래그/줌으로 클릭하면 포커스가 그 iframe으로 넘어가면서
# 브라우저가 자동으로 페이지를 맨 위로 스크롤시켜버리는 경우가 있다. components.html은
# 실제 <script>를 실행할 수 있는 유일한 방법이라 여기서 스크롤 위치를 저장해뒀다가
# 포커스가 빠져나가는 순간(window.parent의 blur) 즉시 복원한다.
components.html(
    """
    <script>
    (function() {
        try {
            var top = window.parent;
            if (top.__moduMapScrollFixInstalled) return;
            top.__moduMapScrollFixInstalled = true;
            var mainEl = top.document.querySelector('[data-testid="stMain"]');
            if (!mainEl) return;
            var lastTop = mainEl.scrollTop;
            mainEl.addEventListener('scroll', function () { lastTop = mainEl.scrollTop; }, { passive: true });
            top.addEventListener('blur', function () {
                requestAnimationFrame(function () {
                    requestAnimationFrame(function () { mainEl.scrollTop = lastTop; });
                });
            });
        } catch (e) {}
    })();
    </script>
    """,
    height=0,
    width=0,
)

render_html(category_title("플랫폼별 상품 비교", top_gap="36px"))

if active_only:
    # 비활성 상품을 blank 처리만 하면 한쪽만 비어 행이 쪼그라들어 보이므로, 아예 제외한 뒤
    # 남은 활성 상품들끼리 순번을 다시 매겨(_pair_products) 짝짓는다.
    selected_pjt_codes = base_filtered["pjt_code"].unique()
    kakao_active = kakao_products[
        kakao_products["pjt_code"].isin(selected_pjt_codes) & kakao_products["is_active"].fillna(False)
    ]
    modu_active = modu_products[
        modu_products["pjt_code"].isin(selected_pjt_codes) & modu_products["is_active"].fillna(False)
    ]
    site_attrs_scope = site_attrs_all[site_attrs_all["pjt_code"].isin(selected_pjt_codes)]
    display_source = _pair_products(kakao_active, modu_active, site_attrs_scope)
    if ticket_selected:
        display_source = display_source[display_source["ticket_type"].isin(ticket_selected)]
else:
    display_source = filtered

display_df = display_source.sort_values(["pjt_code", "ticket_type_rank", "rank"]).reset_index(drop=True).copy()

if show_revenue_cols:
    revenue_df = load_revenue_data(revenue_range[0], revenue_range[1])
    kakao_rev = revenue_df[revenue_df["platform"] == "KAKAO"][
        ["pjt_code", "product_id", "revenue_amount", "revenue_count"]
    ].rename(columns={
        "product_id": "kakao_product_id", "revenue_amount": "kakao_revenue_amount", "revenue_count": "kakao_revenue_count",
    })
    modu_rev = revenue_df[revenue_df["platform"] == "MODU"][
        ["pjt_code", "product_id", "revenue_amount", "revenue_count"]
    ].rename(columns={
        "product_id": "modu_product_id", "revenue_amount": "modu_revenue_amount", "revenue_count": "modu_revenue_count",
    })
    display_df = display_df.merge(kakao_rev, on=["pjt_code", "kakao_product_id"], how="left")
    display_df = display_df.merge(modu_rev, on=["pjt_code", "modu_product_id"], how="left")


def build_export_bytes(export_source, show_revenue_cols):
    # display_df는 렌더링 단계(body_rows 구성)에서만 반복되는 현장명/상품종류를 비워 보여줄 뿐,
    # 데이터 자체에는 매 행마다 값이 채워져 있어 그대로 내보내면 "화면과 같은 값 + 행마다 채움"이 된다.
    kakao_cols = ["kakao_product_name", "kakao_sales_days", "kakao_price", "kakao_stock"]
    modu_cols = ["modu_product_name", "modu_sales_days", "modu_price", "modu_stock"]
    header = ["현장명", "상품종류", "카카오T 상품명", "카카오T 요일", "카카오T 가격", "카카오T 재고"]
    modu_header = ["모두의주차장 상품명", "모두의주차장 요일", "모두의주차장 가격", "모두의주차장 재고"]
    if show_revenue_cols:
        kakao_cols += ["kakao_revenue_amount", "kakao_revenue_count"]
        modu_cols += ["modu_revenue_amount", "modu_revenue_count"]
        header += ["카카오T 매출액", "카카오T 건수"]
        modu_header += ["모두의주차장 매출액", "모두의주차장 건수"]
    header += modu_header

    export_df = export_source[["site_name", "ticket_type"] + kakao_cols + modu_cols].copy()
    export_df["kakao_product_name"] = export_df["kakao_product_name"].fillna("")
    export_df["modu_product_name"] = export_df["modu_product_name"].fillna("")
    export_df["kakao_sales_days"] = export_df["kakao_sales_days"].fillna("")
    export_df["modu_sales_days"] = export_df["modu_sales_days"].fillna("")
    # 원본 상품명/요일 문자열에 엑셀 워크시트가 거부하는 제어 문자가 섞여 들어오는 경우가 있어
    # (예: "월주차권(\x0b평일야간)") 저장 전에 걸러내지 않으면 IllegalCharacterError로 다운로드 자체가 실패한다.
    for col in ["site_name", "ticket_type", "kakao_product_name", "kakao_sales_days", "modu_product_name", "modu_sales_days"]:
        export_df[col] = export_df[col].astype(str).apply(lambda v: ILLEGAL_CHARACTERS_RE.sub("", v))
    export_df.columns = header

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        export_df.to_excel(writer, sheet_name="상품비교", index=False)
    return buffer.getvalue()


_export_scope = "전체현장" if site_selected == "전체" else site_selected.split(" (")[0]
_export_bytes = build_export_bytes(display_df, show_revenue_cols)
st.download_button(
    "⬇️ 현재 화면 표 다운로드 (Excel)",
    data=_export_bytes,
    file_name=f"상품비교_{_export_scope}_{date.today().strftime('%Y%m%d')}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)


def fmt_num(x):
    return "" if pd.isna(x) else f"{x:,.0f}"


for col in ["kakao_price", "kakao_stock", "modu_price", "modu_stock"]:
    display_df[col] = display_df[col].apply(fmt_num)

if show_revenue_cols:
    for col in ["kakao_revenue_amount", "kakao_revenue_count", "modu_revenue_amount", "modu_revenue_count"]:
        display_df[col] = display_df[col].apply(fmt_num)

for col in ["kakao_product_name", "modu_product_name", "kakao_sales_days", "modu_sales_days"]:
    display_df[col] = display_df[col].fillna("")


def esc(v):
    return html.escape(str(v)) if v != "" else ""


# 각 (현장, ticket_type) 그룹에서 한쪽 플랫폼이 완전히 상품이 없으면 그 쪽에는 "-"를
# (첫 행에만), 두 플랫폼 다 없으면 행 전체를 공란으로 두되 상품종류 글씨만 흐리게 표시한다.
# rowspan 대신 그룹 첫 행에만 라벨을 쓰고 나머지 행은 빈 셀로 둔다.
group_cols = ["pjt_code", "ticket_type"]
kakao_group_has = display_df.groupby(group_cols)["kakao_product_name"].transform(lambda s: (s != "").any())
modu_group_has = display_df.groupby(group_cols)["modu_product_name"].transform(lambda s: (s != "").any())

prev_pjt = display_df["pjt_code"].shift(1)
prev_tt = display_df["ticket_type"].shift(1)
is_site_first = display_df["pjt_code"] != prev_pjt
is_tt_first = is_site_first | (display_df["ticket_type"] != prev_tt)

kakao_cols = ["kakao_product_name", "kakao_sales_days", "kakao_price", "kakao_stock"]
modu_cols = ["modu_product_name", "modu_sales_days", "modu_price", "modu_stock"]
if show_revenue_cols:
    kakao_cols += ["kakao_revenue_amount", "kakao_revenue_count"]
    modu_cols += ["modu_revenue_amount", "modu_revenue_count"]
n_platform_cols = len(kakao_cols)

# 가격/재고/매출액/건수는 숫자라 우측 정렬(num-cell)한다. 매출액은 그 앞의 재고 컬럼과
# 성격이 다른 데이터(상품 마스터 정보 vs 기간별 매출 집계)라 세로 구분선(rev-cell)도 준다.
COL_CELL_CLASS = {
    "kakao_price": "num-cell", "kakao_stock": "num-cell",
    "modu_price": "num-cell", "modu_stock": "num-cell",
    "kakao_revenue_amount": "num-cell rev-cell", "kakao_revenue_count": "num-cell",
    "modu_revenue_amount": "num-cell rev-cell", "modu_revenue_count": "num-cell",
}

# 상품종류(정기권/종일권/...) 그룹마다 한 줄씩 건너뛰며 옅은 배경을 줘서, 표가 길어져도
# 어디까지가 같은 그룹인지 한눈에 구분되게 한다.
group_number = is_tt_first.cumsum()

body_rows = []
for idx, row in display_df.iterrows():
    tt_first = bool(is_tt_first.loc[idx])
    site_first = show_site_col and bool(is_site_first.loc[idx])
    kakao_has = bool(kakao_group_has.loc[idx])
    modu_has = bool(modu_group_has.loc[idx])
    both_missing = not kakao_has and not modu_has
    row_cls_parts = (["group-start"] if tt_first else []) + (
        ["zebra-alt"] if int(group_number.loc[idx]) % 2 == 1 else []
    )
    row_cls = f' class="{" ".join(row_cls_parts)}"' if row_cls_parts else ""

    cells = []
    if show_site_col:
        cells.append(f'<td class="group-cell">{esc(row["site_name"])}</td>' if site_first else "<td></td>")

    tt_cls = "group-cell no-data-label" if (tt_first and both_missing) else "group-cell"
    cells.append(f'<td class="{tt_cls}">{esc(row["ticket_type"])}</td>' if tt_first else "<td></td>")

    if both_missing:
        if tt_first:
            cells.append(f'<td colspan="{n_platform_cols * 2}"></td>')
            body_rows.append(f"<tr{row_cls}>{''.join(cells)}</tr>")
        continue

    for side_has, cols in [(kakao_has, kakao_cols), (modu_has, modu_cols)]:
        if side_has:
            for c in cols:
                cls = COL_CELL_CLASS.get(c, "")
                cells.append(f'<td class="{cls}">{esc(row[c])}</td>' if cls else f"<td>{esc(row[c])}</td>")
        elif tt_first:
            cells.append('<td class="dash-cell">-</td>' + "<td></td>" * (n_platform_cols - 1))
        else:
            cells.append("<td></td>" * n_platform_cols)

    body_rows.append(f"<tr{row_cls}>{''.join(cells)}</tr>")

basic_colspan = 2 if show_site_col else 1
site_header = "<th>현장명</th>" if show_site_col else ""
platform_header_extra = (
    '<th class="num-cell rev-cell">매출액</th><th class="num-cell">건수</th>' if show_revenue_cols else ""
)

# 헤더 표와 본문 표를 완전히 분리된 두 개의 <table>로 만들어서(스크롤은 본문에만 적용),
# sticky 헤더 방식에서 나타나던 스크롤 시 겹침/간섭 현상을 없앤다. 두 표가 같은 colgroup을
# 공유해야 컬럼 폭이 서로 어긋나지 않는다.
if show_revenue_cols:
    per_platform_widths = [13, 8, 6, 5, 6, 5] if show_site_col else [13, 9, 7, 5, 7, 6]
    lead_widths = [9, 5] if show_site_col else [6]
else:
    per_platform_widths = [18, 10, 8, 6] if show_site_col else [20, 11, 9, 6]
    lead_widths = [10, 6] if show_site_col else [8]
col_widths = lead_widths + per_platform_widths * 2
colgroup_html = "<colgroup>" + "".join(f'<col style="width:{w}%">' for w in col_widths) + "</colgroup>"

# 카카오T/모두의주차장 각 블록의 첫 컬럼(상품명) 위치에 세로 구분선을 긋는다. 상품종류/
# 현장명 유무와 매출 컬럼 표시 여부에 따라 위치가 바뀌므로 nth-child 인덱스를 계산해서 쓴다.
lead_col_count = len(lead_widths)
kakao_block_start = lead_col_count + 1
modu_block_start = lead_col_count + n_platform_cols + 1

header_table_html = (
    '<table class="compare-table">'
    + colgroup_html +
    "<thead>"
    "<tr>"
    f'<th colspan="{basic_colspan}" class="grp-basic"></th>'
    f'<th colspan="{n_platform_cols}" class="grp-kakao"><img src="{KAKAO_LOGO_URI}" class="brand-logo"/>카카오T</th>'
    f'<th colspan="{n_platform_cols}" class="grp-modu"><img src="{MODU_LOGO_URI}" class="brand-logo brand-logo-modu"/>모두의주차장</th>'
    "</tr>"
    "<tr>"
    f"{site_header}<th>상품종류</th>"
    f'<th>상품명</th><th>요일</th><th class="num-cell">가격</th><th class="num-cell">재고</th>{platform_header_extra}'
    f'<th>상품명</th><th>요일</th><th class="num-cell">가격</th><th class="num-cell">재고</th>{platform_header_extra}'
    "</tr>"
    "</thead>"
    "</table>"
)
body_table_html = (
    '<table class="compare-table">'
    + colgroup_html +
    f"<tbody>{''.join(body_rows)}</tbody>"
    "</table>"
)

render_html(
    f"""
    <style>
    .compare-table-wrap {{ border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }}
    .compare-table-wrap table {{ margin: 0 !important; }}
    .compare-table-body-wrap {{ max-height: 900px; overflow-y: auto; display: block; }}
    /* 고정 px 대신 뷰포트 폭에 비례하는 글씨 크기를 써서, 브라우저 확대/축소로 실질 뷰포트
       폭이 줄어들 때 텍스트가 두 줄로 밀리거나 "..."로 잘리는 대신 글씨가 함께 작아지게 한다. */
    table.compare-table {{ border-collapse: collapse; width: 100%; table-layout: fixed; font-size: clamp(10px, 0.85vw, 14px); color: var(--text-primary); }}
    table.compare-table th.grp-basic {{ background:var(--surface-1); padding:8px 12px; }}
    table.compare-table th.grp-kakao {{ background:var(--surface-1); color:#F5C518; padding:8px 12px; font-weight:500; }}
    table.compare-table th.grp-modu {{ background:var(--surface-1); color:#378ADD; padding:8px 12px; font-weight:500; }}
    table.compare-table .brand-logo {{ height:16px; width:auto; vertical-align:middle; margin-right:6px; }}
    table.compare-table .brand-logo-modu {{ height:20px; border-radius:4px; }}
    table.compare-table thead tr:nth-child(2) th {{ background:var(--surface-1); color:var(--text-secondary); padding:6px 12px; font-weight:500; text-align:left; }}
    /* Streamlit 마크다운 테이블 기본 스타일이 각 행 아래에 border-bottom을 깔아두는데,
       border-collapse:collapse 하에서는 우리가 border:none을 줘도 그 기본 실선을 못 이긴다
       (충돌 해소 규칙상 style:none은 항상 지고, style:hidden만 무조건 이김). 그래서 같은 상품종류
       그룹 안에서도 상품 행마다 원치 않는 가로줄이 보였음 — border-bottom-style만 hidden으로
       명시해서 그 기본값을 무력화한다. */
    table.compare-table td {{ text-align:left; padding:6px 12px; border:none; border-bottom-style:hidden; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
    table.compare-table td.group-cell {{ font-weight:600; vertical-align:middle; }}
    table.compare-table td.no-data-label {{ color:var(--text-muted); }}
    table.compare-table td.dash-cell {{ color:var(--text-muted); }}
    table.compare-table td.num-cell, table.compare-table th.num-cell {{ text-align:right; font-variant-numeric:tabular-nums; }}
    table.compare-table td.rev-cell, table.compare-table th.rev-cell {{ border-left:1px solid var(--border); padding-left:16px; }}
    /* 상품종류/현장명 열과 카카오T 블록 사이, 그리고 카카오T 블록과 모두의주차장 블록 사이
       경계에 세로 구분선을 긋는다(매출 컬럼 표시 여부와 무관하게 항상 같은 블록 경계). */
    table.compare-table thead tr:nth-child(2) th:nth-child({kakao_block_start}),
    table.compare-table tbody td:nth-child({kakao_block_start}),
    table.compare-table thead tr:nth-child(2) th:nth-child({modu_block_start}),
    table.compare-table tbody td:nth-child({modu_block_start}) {{ border-left:1px solid var(--border-strong); }}
    table.compare-table tr.group-start td {{ border-top:0.5px solid var(--border-strong); }}
    table.compare-table tbody tr.zebra-alt {{ background-color: rgba(255,255,255,0.03); }}
    table.compare-table tbody tr:hover {{ background-color: rgba(255,255,255,0.05); }}
    </style>
    """
)
render_html(
    f'<div class="compare-table-wrap">{header_table_html}'
    f'<div class="compare-table-body-wrap">{body_table_html}</div></div>'
)

if site_selected != "전체":
    render_html(category_title("현장 기본 정보", top_gap="36px"))
    pjt_code = site_selected.split("(")[-1].rstrip(")")
    site_info = site_df[site_df["pjt_code"] == pjt_code]
    if not site_info.empty:
        info = site_info.iloc[0]

        open_date = info["open_date"]
        open_date_str = "" if pd.isna(open_date) else str(open_date)
        close_date = info["close_date"]
        close_date_str = "" if pd.isna(close_date) else str(close_date)

        render_html(
            f"""
            <table style="width:100%;border-collapse:collapse;font-size:13px;table-layout:fixed">
            <thead><tr style="border-bottom:0.5px solid var(--border-strong)">
                <th style="text-align:left;padding:8px 6px;color:var(--text-primary);font-weight:500">현장명</th>
                <th style="text-align:left;padding:8px 6px;color:var(--text-primary);font-weight:500">운영담당</th>
                <th style="text-align:left;padding:8px 6px;color:var(--text-primary);font-weight:500">계약유형</th>
                <th style="text-align:left;padding:8px 6px;color:var(--text-primary);font-weight:500">면수</th>
                <th style="text-align:left;padding:8px 6px;color:var(--text-primary);font-weight:500">오픈일</th>
                <th style="text-align:left;padding:8px 6px;color:var(--text-primary);font-weight:500">폐점일</th>
            </tr></thead>
            <tbody><tr>
                <td style="padding:8px 6px">{esc(info["site_name"]) if pd.notna(info["site_name"]) else ""}</td>
                <td style="padding:8px 6px">{esc(info["operation_manager"]) if pd.notna(info["operation_manager"]) else ""}</td>
                <td style="padding:8px 6px">{esc(info["contract_type"]) if pd.notna(info["contract_type"]) else ""}</td>
                <td style="padding:8px 6px">{fmt_num(info["spaces_count"])}</td>
                <td style="padding:8px 6px">{esc(open_date_str)}</td>
                <td style="padding:8px 6px">{esc(close_date_str)}</td>
            </tr></tbody>
            </table>
            """
        )
