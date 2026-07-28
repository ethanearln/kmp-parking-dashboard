import base64
import html
import math
import os
import re

import pandas as pd
import streamlit as st
from google.cloud import bigquery
from google.oauth2 import service_account

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
        --map-h: 15cm;
        --map-w: 10cm;
        --donut-d: 58px;
        --donut-hole: 36px;
    }
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
    </style>
    """
)

# 지도가 어떤 화면에서든 12cm x 20cm로 고정되므로, 같은 행에 놓이는 B구역 나머지
# 요소들(정기권/종일권/도넛)도 뷰포트 비례(vh/vw) 대신 고정 px로 맞춰 지도 높이 안에
# 항상 들어맞도록 한다.
regular_totals = regular_stock_by_category(base_filtered)
day_stock = {d: allday_stock_by_day(base_filtered, d) for d in ["월", "화", "수", "목", "금", "토", "일"]}
weekday_avg = round((day_stock["월"] + day_stock["화"] + day_stock["수"] + day_stock["목"] + day_stock["금"]) / 5)
weekend_avg = round((day_stock["토"] + day_stock["일"]) / 2)

WEEKEND_COLOR = {"토": "var(--text-accent)", "일": "var(--text-danger)"}
REGULAR_ICON_CLASS = {"일반": "ti-ticket", "야간": "ti-moon", "평일": "ti-briefcase", "휴일": "ti-sun"}

weekday_days_html = "".join(
    f"""
    <div style="text-align:center">
        <p style="font-size:12px;color:var(--text-muted);margin:0 0 4px">{d}</p>
        <p style="font-size:17px;font-weight:500;margin:0;color:var(--text-primary)">{day_stock[d]:,.0f}</p>
    </div>
    """
    for d in ["월", "화", "수", "목", "금"]
)
weekend_days_html = "".join(
    f"""
    <div style="text-align:center">
        <p style="font-size:12px;color:{WEEKEND_COLOR[d]};margin:0 0 4px">{d}</p>
        <p style="font-size:17px;font-weight:500;margin:0;color:{WEEKEND_COLOR[d]}">{day_stock[d]:,.0f}</p>
    </div>
    """
    for d in ["토", "일"]
)

regular_items_html = "".join(
    f"""
    <div style="background:var(--surface-1);border-radius:var(--radius);padding:14px 12px;display:flex;align-items:center;gap:10px;height:100%;box-sizing:border-box">
        <i class="ti {REGULAR_ICON_CLASS[label]}" style="font-size:22px;color:var(--text-secondary)" aria-hidden="true"></i>
        <div>
            <p style="font-size:13px;color:var(--text-muted);margin:0">{label}</p>
            <p style="font-size:26px;font-weight:600;margin:0;color:var(--text-primary)">{regular_totals[label]:,.0f}</p>
        </div>
    </div>
    """
    for label in ["일반", "야간", "평일", "휴일"]
)

# --- 상품종류별 플랫폼 재고 비중(도넛): B구역에서 정기권/종일권과 한 열에 배치해야 해서
# 표(C구역)보다 먼저 계산한다 ---
PLATFORM_SLICE_COLOR = {"카카오T": "#F5C518", "모두의주차장": "#378ADD"}


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

DONUT_OUTER_R_PX = 29  # --donut-d(58px) 반지름
DONUT_HOLE_R_PX = 18  # --donut-hole(36px) 반지름
DONUT_MID_R_PX = (DONUT_OUTER_R_PX + DONUT_HOLE_R_PX) / 2


def _donut_pct_label(pct_value, mid_angle_deg, color):
    # mid_angle_deg: 12시 방향(0도)에서 시계방향으로 잰 슬라이스 중앙 각도.
    # transform은 위치 이동(translate)에만 쓰고 회전(rotate)은 걸지 않아 글자가 항상 수평을 유지한다.
    theta = math.radians(mid_angle_deg)
    x = DONUT_MID_R_PX * math.sin(theta)
    y = -DONUT_MID_R_PX * math.cos(theta)
    return (
        f'<span style="position:absolute;left:calc(50% + {x:.1f}px);top:calc(50% + {y:.1f}px);'
        f'transform:translate(-50%,-50%);font-size:10px;font-weight:600;color:{color};'
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

    donut_cards_html += f"""
    <div style="background:var(--surface-1);border-radius:var(--radius);padding:6px 5px;text-align:center;height:100%;box-sizing:border-box;display:flex;flex-direction:column;justify-content:center;overflow:hidden">
        <div style="width:var(--donut-d);height:var(--donut-d);border-radius:50%;margin:0 auto 5px;position:relative;flex:none">
            <div style="width:100%;height:100%;border-radius:50%;
                        background:conic-gradient({PLATFORM_SLICE_COLOR['카카오T']} 0% {kakao_pct:.4f}%,{PLATFORM_SLICE_COLOR['모두의주차장']} {kakao_pct:.4f}% 100%)"></div>
            <div style="position:absolute;top:calc((var(--donut-d) - var(--donut-hole)) / 2);left:calc((var(--donut-d) - var(--donut-hole)) / 2);width:var(--donut-hole);height:var(--donut-hole);border-radius:50%;background:var(--surface-1)"></div>
            {pct_labels_html}
        </div>
        <p style="font-size:12px;color:var(--text-secondary);margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{tt}</p>
        <p style="font-size:9px;color:var(--text-muted);margin:2px 0 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{kakao_sum:,.0f}건 · {modu_sum:,.0f}건</p>
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
    <div style="flex:1;min-height:0;display:flex;flex-direction:column">
        {category_title("정기권 재고 현황")}
        <div style="flex:1;min-height:0;display:grid;grid-auto-rows:1fr;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:8px">{regular_items_html}</div>
    </div>
    <div style="flex:1;min-height:0;display:flex;flex-direction:column">
        {category_title("종일권 재고 현황")}
        <div style="flex:1;min-height:0;display:flex;gap:8px">
            <div style="flex:5;background:var(--surface-1);border-radius:var(--radius);overflow:hidden;display:flex;flex-direction:column">
                <div style="padding:10px 12px;text-align:center;border-bottom:0.5px solid var(--border)">
                    <p style="font-size:12px;color:var(--text-muted);margin:0 0 3px">평일 평균 (월~금)</p>
                    <p style="font-size:21px;font-weight:600;margin:0;color:var(--text-primary)">{weekday_avg:,.0f}</p>
                </div>
                <div style="flex:1;min-height:0;display:grid;grid-template-columns:repeat(5,1fr);align-content:center;padding:8px 12px">{weekday_days_html}</div>
            </div>
            <div style="flex:2;background:var(--surface-1);border-radius:var(--radius);overflow:hidden;display:flex;flex-direction:column">
                <div style="padding:10px 12px;text-align:center;border-bottom:0.5px solid var(--border)">
                    <p style="font-size:12px;color:var(--text-muted);margin:0 0 3px">휴일 평균 (토~일)</p>
                    <p style="font-size:21px;font-weight:600;margin:0;color:var(--text-primary)">{weekend_avg:,.0f}</p>
                </div>
                <div style="flex:1;min-height:0;display:grid;grid-template-columns:repeat(2,1fr);align-content:center;padding:8px 12px">{weekend_days_html}</div>
            </div>
        </div>
    </div>
    <div style="flex:1;min-height:0;display:flex;flex-direction:column">
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

render_html(category_title("플랫폼별 상품 비교", top_gap="36px"))

display_df = filtered.sort_values(["pjt_code", "ticket_type_rank", "rank"]).reset_index(drop=True).copy()


def fmt_num(x):
    return "" if pd.isna(x) else f"{x:,.0f}"


for col in ["kakao_price", "kakao_stock", "modu_price", "modu_stock"]:
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

body_rows = []
for idx, row in display_df.iterrows():
    tt_first = bool(is_tt_first.loc[idx])
    site_first = show_site_col and bool(is_site_first.loc[idx])
    kakao_has = bool(kakao_group_has.loc[idx])
    modu_has = bool(modu_group_has.loc[idx])
    both_missing = not kakao_has and not modu_has
    row_cls = ' class="group-start"' if tt_first else ""

    cells = []
    if show_site_col:
        cells.append(f'<td class="group-cell">{esc(row["site_name"])}</td>' if site_first else "<td></td>")

    tt_cls = "group-cell no-data-label" if (tt_first and both_missing) else "group-cell"
    cells.append(f'<td class="{tt_cls}">{esc(row["ticket_type"])}</td>' if tt_first else "<td></td>")

    if both_missing:
        if tt_first:
            cells.append('<td colspan="8"></td>')
            body_rows.append(f"<tr{row_cls}>{''.join(cells)}</tr>")
        continue

    for side_has, cols in [
        (kakao_has, ["kakao_product_name", "kakao_sales_days", "kakao_price", "kakao_stock"]),
        (modu_has, ["modu_product_name", "modu_sales_days", "modu_price", "modu_stock"]),
    ]:
        if side_has:
            for c in cols:
                cells.append(f"<td>{esc(row[c])}</td>")
        elif tt_first:
            cells.append('<td class="dash-cell">-</td><td></td><td></td><td></td>')
        else:
            cells.append("<td></td><td></td><td></td><td></td>")

    body_rows.append(f"<tr{row_cls}>{''.join(cells)}</tr>")

basic_colspan = 2 if show_site_col else 1
site_header = "<th>현장명</th>" if show_site_col else ""

table_html = (
    '<table class="compare-table">'
    "<thead>"
    "<tr>"
    f'<th colspan="{basic_colspan}" class="grp-basic"></th>'
    f'<th colspan="4" class="grp-kakao"><img src="{KAKAO_LOGO_URI}" class="brand-logo"/>카카오T</th>'
    f'<th colspan="4" class="grp-modu"><img src="{MODU_LOGO_URI}" class="brand-logo brand-logo-modu"/>모두의주차장</th>'
    "</tr>"
    "<tr>"
    f"{site_header}<th>상품종류</th>"
    "<th>상품명</th><th>요일</th><th>가격</th><th>재고</th>"
    "<th>상품명</th><th>요일</th><th>가격</th><th>재고</th>"
    "</tr>"
    "</thead>"
    f"<tbody>{''.join(body_rows)}</tbody>"
    "</table>"
)

render_html(
    """
    <style>
    .compare-table-wrap { max-height: 960px; overflow-y: auto; border: 1px solid var(--border); border-radius: 6px; }
    table.compare-table { border-collapse: collapse; width: 100%; font-size: 14px; color: var(--text-primary); }
    table.compare-table th.grp-basic { background:transparent; padding:8px 12px; position:sticky; top:0; z-index:2; }
    table.compare-table th.grp-kakao { background:#FAEEDA; color:#412402; padding:8px 12px; position:sticky; top:0; z-index:2; font-weight:500; }
    table.compare-table th.grp-modu { background:#E6F1FB; color:#042C53; padding:8px 12px; position:sticky; top:0; z-index:2; font-weight:500; }
    table.compare-table .brand-logo { height:16px; width:auto; vertical-align:middle; margin-right:6px; }
    table.compare-table .brand-logo-modu { height:20px; border-radius:4px; }
    table.compare-table thead tr:nth-child(2) th { background:var(--surface-1); color:var(--text-secondary); padding:6px 12px; font-weight:500; text-align:left; position:sticky; top:36px; z-index:2; }
    table.compare-table td { text-align:left; padding:6px 12px; border:none; }
    table.compare-table td.group-cell { font-weight:600; vertical-align:middle; }
    table.compare-table td.no-data-label { color:var(--text-muted); }
    table.compare-table td.dash-cell { color:var(--text-muted); }
    table.compare-table tr.group-start td { border-top:0.5px solid var(--border-strong); }
    table.compare-table tbody tr:hover { background-color: rgba(255,255,255,0.05); }
    </style>
    """
)
render_html(f'<div class="compare-table-wrap">{table_html}</div>')

if site_selected != "전체":
    render_html(category_title("현장 기본 정보", top_gap="36px"))
    pjt_code = site_selected.split("(")[-1].rstrip(")")
    site_info = site_df[site_df["pjt_code"] == pjt_code]
    if not site_info.empty:
        info = site_info.iloc[0]

        def _first_valid_id(series):
            valid = series.dropna()
            valid = valid[valid.astype(str).str.strip() != ""]
            return valid.iloc[0] if not valid.empty else None

        # 하나의 pjt_code에 site 행이 여러 개 붙어있을 수 있어(예: 미사용 연동이 남은 행),
        # 연동 ID는 첫 행이 아니라 값이 있는 행을 우선으로 가져온다.
        site_ids = ", ".join(
            str(v) for v in [
                _first_valid_id(site_info["kakao_site_id"]),
                _first_valid_id(site_info["modu_site_id"]),
            ] if v is not None
        )
        open_date = info["open_date"]
        open_date_str = "" if pd.isna(open_date) else str(open_date)

        render_html(
            f"""
            <table style="width:100%;border-collapse:collapse;font-size:13px;table-layout:fixed">
            <thead><tr style="border-bottom:0.5px solid var(--border-strong)">
                <th style="text-align:left;padding:8px 6px;color:var(--text-primary);font-weight:500">현장명</th>
                <th style="text-align:left;padding:8px 6px;color:var(--text-primary);font-weight:500">운영담당</th>
                <th style="text-align:left;padding:8px 6px;color:var(--text-primary);font-weight:500">계약유형</th>
                <th style="text-align:left;padding:8px 6px;color:var(--text-primary);font-weight:500">구획수</th>
                <th style="text-align:left;padding:8px 6px;color:var(--text-primary);font-weight:500">개장일</th>
                <th style="text-align:left;padding:8px 6px;color:var(--text-primary);font-weight:500">연동 ID</th>
            </tr></thead>
            <tbody><tr>
                <td style="padding:8px 6px">{esc(info["site_name"]) if pd.notna(info["site_name"]) else ""}</td>
                <td style="padding:8px 6px">{esc(info["operation_manager"]) if pd.notna(info["operation_manager"]) else ""}</td>
                <td style="padding:8px 6px">{esc(info["contract_type"]) if pd.notna(info["contract_type"]) else ""}</td>
                <td style="padding:8px 6px">{fmt_num(info["spaces_count"])}</td>
                <td style="padding:8px 6px">{esc(open_date_str)}</td>
                <td style="padding:8px 6px;color:var(--text-muted);font-size:12px">{esc(site_ids)}</td>
            </tr></tbody>
            </table>
            """
        )
