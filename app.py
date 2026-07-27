import base64
import html
import os
import re

import pandas as pd
import plotly.express as px
import streamlit as st
from google.cloud import bigquery
from google.oauth2 import service_account

st.set_page_config(page_title="현장별 플랫폼 상품 비교", layout="wide")
px.defaults.template = "plotly_dark"

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


@st.cache_data(ttl=86400)  # 원본 테이블은 하루 1회(UTC 01:00경) 갱신되므로 굳이 자주 재조회할 필요가 없다.
def load_kakao_products():
    client = get_bigquery_client()
    # 서비스 중 + 판매중인(현재 실제로 팔리고 있는) 상품만 대상으로 한다.
    # 이렇게 하면 하나의 pjt_code에 카카오 연동이 여러 개 걸려 있어도
    # 미사용/중지된 연동은 자연스럽게 제외되고 실제 운영 중인 연동만 남는다.
    query = f"""
        SELECT parking_lot_id, ticket_type, product_name,
               base_price AS price, base_stock_qty AS stock, sales_days
        FROM `{PROJECT}.db_platform_kakao.tb_product_data_kakao_recent`
        WHERE service_status = '서비스 중' AND ticket_on_off = '판매중'
    """
    return client.query(query).to_dataframe()


@st.cache_data(ttl=86400)  # 원본 테이블은 하루 1회(UTC 01:00경) 갱신되므로 굳이 자주 재조회할 필요가 없다.
def load_modu_products():
    client = get_bigquery_client()
    query = f"""
        SELECT parking_lot_id, ticket_type, ticket_name AS product_name,
               price, limit_quantity AS stock, sales_days
        FROM `{PROJECT}.db_platform_modu.tb_product_data_modu_recent`
        WHERE sales_status = '판매중'
    """
    return client.query(query).to_dataframe()


def _attach_pjt_code(products, site_df, id_col):
    site_map = site_df[["pjt_code", id_col]].dropna(subset=[id_col]).drop_duplicates()
    merged = products.merge(site_map, left_on="parking_lot_id", right_on=id_col, how="inner")
    return merged.drop(columns=[id_col, "parking_lot_id"])


def _rank_products(products):
    # 동일 (현장, ticket_type) 안에서 product_name 내림차순 -> price 내림차순으로 정렬한 뒤
    # 순번을 매겨 카카오T/모두의주차장 상품을 같은 줄에 나란히 비교할 수 있게 짝짓는다.
    ranked = products.sort_values(["product_name", "price"], ascending=[False, False]).copy()
    ranked["rank"] = ranked.groupby(["pjt_code", "ticket_type"]).cumcount()
    return ranked


@st.cache_data(ttl=86400)  # 원본 테이블은 하루 1회(UTC 01:00경) 갱신되므로 굳이 자주 재조회할 필요가 없다.
def load_product_data():
    site_df = load_site_data()

    kakao = _attach_pjt_code(load_kakao_products(), site_df, "kakao_site_id")
    modu = _attach_pjt_code(load_modu_products(), site_df, "modu_site_id")

    for products in (kakao, modu):
        products["ticket_type"] = products["ticket_type"].where(
            products["ticket_type"].isin(TICKET_TYPE_ORDER), "미분류"
        )

    kakao_r = _rank_products(kakao)[
        ["pjt_code", "ticket_type", "rank", "product_name", "price", "stock", "sales_days"]
    ].rename(columns={
        "product_name": "kakao_product_name", "price": "kakao_price",
        "stock": "kakao_stock", "sales_days": "kakao_sales_days",
    })
    modu_r = _rank_products(modu)[
        ["pjt_code", "ticket_type", "rank", "product_name", "price", "stock", "sales_days"]
    ].rename(columns={
        "product_name": "modu_product_name", "price": "modu_price",
        "stock": "modu_stock", "sales_days": "modu_sales_days",
    })

    paired = pd.merge(kakao_r, modu_r, on=["pjt_code", "ticket_type", "rank"], how="outer")

    # pjt_code 는 중복 제거 없이 그대로 "존재하는 현장 목록"의 기준이 된다.
    # 상품이 있건 없건 모든 (현장, ticket_type) 조합이 노출되도록 스캐폴드를 만든다.
    site_attrs = site_df.groupby("pjt_code", as_index=False).agg({
        "site_name": "first", "operation_hq": "first", "operation_manager": "first",
        "contract_type": "first", "spaces_count": "first",
    })
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


df = load_product_data()
site_df = load_site_data()

render_html(
    """
    <style>
    @import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css');
    html, body, [class*="css"] { font-family: 'Pretendard', -apple-system, 'Malgun Gothic', sans-serif; }
    </style>
    """
)

st.sidebar.header("필터")

hq_options = sorted(df["operation_hq"].dropna().unique())
hq_selected = st.sidebar.multiselect("운영본부", hq_options)

df["site_label"] = df["site_name"] + " (" + df["pjt_code"] + ")"
site_options = sorted(df["site_label"].dropna().unique())
site_selected = st.sidebar.selectbox("현장명", ["전체"] + site_options)

ticket_options = sorted(df["ticket_type"].dropna().unique())
ticket_selected = st.sidebar.multiselect("상품종류", ticket_options)

base_filtered = df.copy()
if hq_selected:
    base_filtered = base_filtered[base_filtered["operation_hq"].isin(hq_selected)]
if site_selected != "전체":
    base_filtered = base_filtered[base_filtered["site_label"] == site_selected]

filtered = base_filtered.copy()
if ticket_selected:
    filtered = filtered[filtered["ticket_type"].isin(ticket_selected)]

show_site_col = site_selected == "전체"
header_subtitle = (
    "전체 현장 · 카카오T vs 모두의주차장"
    if show_site_col
    else f"{site_selected.split(' (')[0]} · 카카오T vs 모두의주차장"
)
render_html(
    f"""
    <div style="margin-bottom:12px;">
        <div style="font-size:clamp(22px,2.4vw,30px);font-weight:800;color:#fff;">현장별 플랫폼 상품 비교 대시보드</div>
        <div style="font-size:clamp(13px,1.1vw,15px);color:#9aa0a6;margin-top:4px;">{header_subtitle}</div>
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


def render_modu_map_panel(site_selected, site_df):
    placeholder = (
        '<div class="map-panel"><div class="map-placeholder">{msg}</div></div>'
    )
    if site_selected == "전체":
        render_html(placeholder.format(msg="현장을 선택하면<br/>모두의주차장 지도가<br/>표시됩니다."))
        return

    pjt_code = site_selected.split("(")[-1].rstrip(")")
    site_info = site_df[site_df["pjt_code"] == pjt_code]
    # 하나의 pjt_code에 site 행이 여러 개 붙어있을 수 있고(예: 미사용 카카오 연동이 남아있는 행),
    # 그 중 일부만 modu_site_id를 갖고 있을 수 있다. 행 순서는 BigQuery가 보장해주지 않으므로
    # 단순히 첫 행(iloc[0])을 쓰면 값이 있는데도 없는 것처럼 보일 수 있어, 값이 있는 행을 우선한다.
    valid_ids = site_info["modu_site_id"].dropna()
    valid_ids = valid_ids[valid_ids.astype(str).str.strip() != ""]
    modu_site_id = valid_ids.iloc[0] if not valid_ids.empty else None
    if site_info.empty or pd.isna(modu_site_id) or not str(modu_site_id).strip():
        render_html(placeholder.format(msg="이 현장은 모두의주차장<br/>연동 ID가 없어 지도를<br/>표시할 수 없습니다."))
        return

    modu_map_url = f"https://app.modu.kr/map?type=P&id={modu_site_id}#sheet=1&event=0"
    # st.iframe은 서버에서 고정 px 높이를 미리 예약해버려 뷰포트 크기에 반응할 수 없다.
    # 원시 <iframe>을 CSS(aspect-ratio)로 감싸면 창 크기가 바뀔 때 다시 실행 없이도
    # 브라우저가 알아서 1:1.8 비율을 유지한 채 리사이즈해준다.
    render_html(f'<div class="map-panel"><iframe src="{modu_map_url}"></iframe></div>')


render_html(
    """
    <style>
    .map-panel {
        background:#1b1e24; border-radius:10px; overflow:hidden;
        width: min(6cm, 100%); aspect-ratio: 1 / 1.8; margin: 0 auto;
    }
    .map-panel iframe { width:100%; height:100%; border:0; display:block; }
    .map-placeholder {
        height:100%; box-sizing:border-box; padding:16px; display:flex;
        align-items:center; justify-content:center; text-align:center;
        color:#9aa0a6; font-size:13px; line-height:1.6;
    }
    .stat-item { text-align:center; }
    .stat-label { color:#ccc; font-size: clamp(12px, 1vw, 14px); white-space:nowrap; }
    .stat-value { color:#fff; font-weight:700; font-size: clamp(18px, 2vw, 28px); white-space:nowrap; }
    .stat-row {
        display:flex; flex-wrap:wrap; align-items:center; justify-content:space-around;
        gap: clamp(10px, 1.5vw, 20px) 28px; margin: 18px 0 30px;
    }
    .allday-grid { display:flex; flex-wrap:wrap; margin: 12px 0 28px; }
    .allday-col { flex:1; min-width:220px; text-align:center; padding:0 24px; box-sizing:border-box; }
    .allday-col + .allday-col { border-left:1px solid #2a2d35; }
    .allday-heading { color:#9aa0a6; font-size: clamp(12px, 1vw, 14px); }
    .allday-value { color:#fff; font-weight:800; font-size: clamp(24px, 2.4vw, 32px); margin:8px 0 18px; }
    .allday-days {
        display:flex; flex-wrap:wrap; justify-content:center;
        gap: clamp(8px, 1.5vw, 18px) 20px; padding-top:14px; border-top:1px solid #2a2d35;
    }
    </style>
    """
)

regular_totals = regular_stock_by_category(base_filtered)
day_stock = {d: allday_stock_by_day(base_filtered, d) for d in ["월", "화", "수", "목", "금", "토", "일"]}
weekday_avg = round((day_stock["월"] + day_stock["화"] + day_stock["수"] + day_stock["목"] + day_stock["금"]) / 5)
weekend_avg = round((day_stock["토"] + day_stock["일"]) / 2)

day_colors = {"월": "#eee", "화": "#eee", "수": "#eee", "목": "#eee", "금": "#eee", "토": "#5B9BD5", "일": "#E06666"}
REGULAR_ICONS = {"일반": "🎫", "야간": "🌙", "평일": "💼", "휴일": "☀️"}

weekday_days_html = "".join(
    f"""
    <div class="stat-item">
        <div class="stat-label" style="color:{day_colors[d]};">{d}</div>
        <div class="stat-value" style="color:{day_colors[d]};">{day_stock[d]:,.0f}</div>
    </div>
    """
    for d in ["월", "화", "수", "목", "금"]
)
weekend_days_html = "".join(
    f"""
    <div class="stat-item">
        <div class="stat-label" style="color:{day_colors[d]};">{d}</div>
        <div class="stat-value" style="color:{day_colors[d]};">{day_stock[d]:,.0f}</div>
    </div>
    """
    for d in ["토", "일"]
)

regular_items_html = "".join(
    f"""
    <div class="stat-item">
        <div class="stat-label">{REGULAR_ICONS[label]} {label}</div>
        <div class="stat-value">{regular_totals[label]:,.0f}</div>
    </div>
    """
    for label in ["일반", "야간", "평일", "휴일"]
)

st.subheader("정기권 재고 현황")
render_html(f'<div class="stat-row">{regular_items_html}</div>')

st.subheader("종일권 재고 현황")
render_html(
    f"""
    <div class="allday-grid">
        <div class="allday-col">
            <div class="allday-heading">평일 평균 (월~금)</div>
            <div class="allday-value">{weekday_avg:,.0f}</div>
            <div class="allday-days">{weekday_days_html}</div>
        </div>
        <div class="allday-col">
            <div class="allday-heading">휴일 평균 (토~일)</div>
            <div class="allday-value">{weekend_avg:,.0f}</div>
            <div class="allday-days">{weekend_days_html}</div>
        </div>
    </div>
    """
)

st.divider()
st.subheader("플랫폼별 상품 비교")

display_df = filtered.sort_values(["pjt_code", "ticket_type_rank", "rank"]).reset_index(drop=True).copy()


def fmt_num(x):
    return "" if pd.isna(x) else f"{x:,.0f}"


for col in ["kakao_price", "kakao_stock", "modu_price", "modu_stock"]:
    display_df[col] = display_df[col].apply(fmt_num)

for col in ["kakao_product_name", "modu_product_name"]:
    display_df[col] = display_df[col].fillna("")


def esc(v):
    return html.escape(str(v)) if v != "" else ""


# 동일 (현장, ticket_type) 그룹은 rowspan으로 한 번만 표시하고, 그룹이 바뀔 때만
# 구분선을 그려서 이미지처럼 그룹 단위로 깔끔하게 묶인 표를 만든다.
site_group_sizes = display_df.groupby("pjt_code")["pjt_code"].transform("size")
tt_group_sizes = display_df.groupby(["pjt_code", "ticket_type"])["ticket_type"].transform("size")
prev_pjt = display_df["pjt_code"].shift(1)
prev_tt = display_df["ticket_type"].shift(1)
is_site_first = display_df["pjt_code"] != prev_pjt
is_tt_first = is_site_first | (display_df["ticket_type"] != prev_tt)

body_rows = []
for idx, row in display_df.iterrows():
    cells = []
    if show_site_col and is_site_first.loc[idx]:
        cells.append(
            f'<td rowspan="{int(site_group_sizes.loc[idx])}" class="group-cell">{esc(row["site_name"])}</td>'
        )

    tt_first = bool(is_tt_first.loc[idx])
    row_cls = ' class="group-start"' if tt_first else ""

    if tt_first:
        cells.append(
            f'<td rowspan="{int(tt_group_sizes.loc[idx])}" class="group-cell">{esc(row["ticket_type"])}</td>'
        )

    is_empty_group = (
        tt_first and int(tt_group_sizes.loc[idx]) == 1
        and row["kakao_product_name"] == "" and row["modu_product_name"] == ""
    )
    if is_empty_group:
        cells.append('<td colspan="6" class="no-data-cell">데이터 없음</td>')
    else:
        for c in ["kakao_product_name", "kakao_price", "kakao_stock",
                  "modu_product_name", "modu_price", "modu_stock"]:
            cells.append(f"<td>{esc(row[c])}</td>")

    body_rows.append(f"<tr{row_cls}>{''.join(cells)}</tr>")

basic_colspan = 2 if show_site_col else 1
site_header = "<th>현장명</th>" if show_site_col else ""

table_html = (
    '<table class="compare-table">'
    "<thead>"
    "<tr>"
    f'<th colspan="{basic_colspan}" class="grp-basic"></th>'
    f'<th colspan="3" class="grp-kakao"><img src="{KAKAO_LOGO_URI}" class="brand-logo"/>카카오T</th>'
    f'<th colspan="3" class="grp-modu"><img src="{MODU_LOGO_URI}" class="brand-logo brand-logo-modu"/>모두의주차장</th>'
    "</tr>"
    "<tr>"
    f"{site_header}<th>상품종류</th>"
    "<th>상품명</th><th>가격</th><th>재고</th>"
    "<th>상품명</th><th>가격</th><th>재고</th>"
    "</tr>"
    "</thead>"
    f"<tbody>{''.join(body_rows)}</tbody>"
    "</table>"
)

render_html(
    """
    <style>
    .compare-table-wrap { max-height: 960px; overflow-y: auto; border: 1px solid #2a2d35; border-radius: 6px; }
    table.compare-table { border-collapse: collapse; width: 100%; font-size: 14px; color: #ddd; }
    table.compare-table th.grp-basic { background:transparent; padding:8px 12px; position:sticky; top:0; z-index:2; }
    table.compare-table th.grp-kakao { background:#F6E4C6; color:#3a2a10; padding:8px 12px; position:sticky; top:0; z-index:2; }
    table.compare-table th.grp-modu { background:#D6EAFB; color:#0d3a5c; padding:8px 12px; position:sticky; top:0; z-index:2; }
    table.compare-table .brand-logo { height:16px; width:auto; vertical-align:middle; margin-right:6px; }
    table.compare-table .brand-logo-modu { height:20px; border-radius:4px; }
    table.compare-table thead tr:nth-child(2) th { background:#262a33; color:#ddd; padding:6px 12px; font-weight:600; text-align:left; position:sticky; top:36px; z-index:2; }
    table.compare-table td { text-align:left; padding:6px 12px; border:none; }
    table.compare-table td.group-cell { font-weight:600; vertical-align:middle; }
    table.compare-table td.no-data-cell { color:#7a8087; text-align:center; }
    table.compare-table tr.group-start td { border-top:1px solid #2a2d35; }
    table.compare-table tbody tr:hover { background-color: rgba(255,255,255,0.05); }
    </style>
    """
)
render_html(f'<div class="compare-table-wrap">{table_html}</div>')

st.divider()
st.subheader("상품종류별 플랫폼 재고 비중")

PLATFORM_SLICE_COLOR = {"카카오T": "#FFA94D", "모두의주차장": "#3B9EFF"}


def ticket_type_sort_key(tt):
    return (TICKET_TYPE_ORDER.index(tt) if tt in TICKET_TYPE_ORDER else len(TICKET_TYPE_ORDER), tt)


ticket_types = sorted(filtered["ticket_type"].dropna().unique(), key=ticket_type_sort_key)
plat_totals = []
for tt in ticket_types:
    sub = filtered[filtered["ticket_type"] == tt]
    kakao_sum = sub["kakao_stock"].fillna(0).sum()
    modu_sum = sub["modu_stock"].fillna(0).sum()
    if kakao_sum + modu_sum > 0:
        plat_totals.append((tt, kakao_sum, modu_sum))

# 모두의주차장 지도는 상품종류별 재고 비중(도넛 차트)과 나란히 배치한다.
donut_col, map_col = st.columns([5, 2])

with donut_col:
    render_html(
        f"""
        <div style="display:flex;justify-content:center;gap:32px;margin-bottom:12px;align-items:center;">
            <div style="display:flex;align-items:center;gap:8px;">
                <span style="width:12px;height:12px;background:{PLATFORM_SLICE_COLOR['카카오T']};border-radius:50%;display:inline-block;"></span>
                <span style="color:#ddd;font-size:14px;">카카오T</span>
            </div>
            <div style="display:flex;align-items:center;gap:8px;">
                <span style="width:12px;height:12px;background:{PLATFORM_SLICE_COLOR['모두의주차장']};border-radius:50%;display:inline-block;"></span>
                <span style="color:#ddd;font-size:14px;">모두의주차장</span>
            </div>
        </div>
        """
    )

    if plat_totals:
        cols = st.columns(len(plat_totals), gap="medium")
        for col, (tt, kakao_sum, modu_sum) in zip(cols, plat_totals):
            plat_df = pd.DataFrame({"플랫폼": ["카카오T", "모두의주차장"], "재고": [kakao_sum, modu_sum]})
            fig = px.pie(
                plat_df,
                names="플랫폼",
                values="재고",
                hole=0.55,
                color="플랫폼",
                color_discrete_map=PLATFORM_SLICE_COLOR,
            )
            fig.update_traces(
                textinfo="none",
                hovertemplate="%{label}<br>재고 합계: %{value:,.0f}<br>비중: %{percent}<extra></extra>",
            )
            fig.update_layout(showlegend=False, height=180, margin=dict(t=10, b=10, l=10, r=10))
            with col:
                st.plotly_chart(fig, use_container_width=True)
                render_html(
                    f"""
                    <div style="text-align:center;margin-top:-8px;">
                        <div style="color:#ddd;font-size:14px;font-weight:600;">{tt}</div>
                        <div style="color:#9aa0a6;font-size:13px;">{kakao_sum:,.0f} · {modu_sum:,.0f}</div>
                    </div>
                    """
                )
    else:
        st.caption("표시할 재고 데이터가 없습니다.")

with map_col:
    render_modu_map_panel(site_selected, site_df)

if site_selected != "전체":
    st.divider()
    st.subheader("현장 기본 정보")
    pjt_code = site_selected.split("(")[-1].rstrip(")")
    site_info = site_df[site_df["pjt_code"] == pjt_code]
    if not site_info.empty:
        st.dataframe(site_info, use_container_width=True)
