import streamlit as st
import pandas as pd
import re
import io
import os

def parse_employee_info(sheet_df):
    for i, row in sheet_df.iterrows():
        row_str = " ".join(str(v) for v in row.values if pd.notna(v))
        if "Employee ID" in row_str:
            emp_id = re.search(r"Employee ID[:\s]+(\S+)", row_str)
            emp_name = re.search(r"Employee Name[:\s]+([A-Za-z\s]+?)(?=Department|$)", row_str)
            dept = re.search(r"Department Name[:\s]+(.+)", row_str)
            return (
                emp_id.group(1).strip() if emp_id else None,
                emp_name.group(1).strip() if emp_name else None,
                dept.group(1).strip() if dept else None,
            )
    return None, None, None

def find_table_header_row(sheet_df):
    for i, row in sheet_df.iterrows():
        if "Punch Date" in row.values:
            return i
    return None

def rename_duplicate_columns(columns):
    counts = {}
    for col in columns:
        counts[col] = counts.get(col, 0) + 1
    seen = {}
    result = []
    for col in columns:
        if counts[col] > 1:
            seen[col] = seen.get(col, 0) + 1
            result.append(f"{col} {seen[col]}")
        else:
            result.append(col)
    return result

def parse_hhmm_to_minutes(val):
    if pd.isna(val):
        return 0
    if isinstance(val, pd.Timedelta):
        return int(val.total_seconds() // 60)
    match = re.match(r"(\d+):([0-5]\d)", str(val).strip())
    return int(match.group(1)) * 60 + int(match.group(2)) if match else 0

def minutes_to_hhmm(minutes):
    minutes = max(0, int(minutes))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"

def recalculate_ot(df):
    cols = list(df.columns)
    clock_in_map = {re.sub(r"(?i)clock\s*in\s*", "", c).strip(): c
                    for c in cols if re.search(r"(?i)clock\s*in", c)}
    clock_out_map = {re.sub(r"(?i)clock\s*out\s*", "", c).strip(): c
                     for c in cols if re.search(r"(?i)clock\s*out", c)}
    total_time_map = {re.sub(r"(?i)total\s*time\s*", "", c).strip(): c
                      for c in cols if re.search(r"(?i)total\s*time", c)}

    for suffix in clock_in_map:
        ci_col = clock_in_map[suffix]
        co_col = clock_out_map.get(suffix)
        tt_col = total_time_map.get(suffix)
        if co_col and tt_col:
            df[tt_col] = df.apply(
                lambda row, ci=ci_col, co=co_col: minutes_to_hhmm(
                    max(0, parse_hhmm_to_minutes(row[co]) - parse_hhmm_to_minutes(row[ci]))
                ), axis=1
            )

    time_cols = [c for c in cols if re.search(r"(?i)total\s*time", c)]
    wt_col = next((c for c in cols if c not in ("OT", "Total WT - OT")
                   and ("WT" in str(c).upper() or "WORK" in str(c).upper())), None)
    if time_cols and wt_col:
        df[wt_col] = df[time_cols].apply(
            lambda row: minutes_to_hhmm(sum(parse_hhmm_to_minutes(v) for v in row)), axis=1
        )
    if wt_col:
        standard_minutes = 8 * 60
        df["OT"] = df[wt_col].apply(
            lambda v: minutes_to_hhmm(parse_hhmm_to_minutes(v) - standard_minutes)
        )
        df["Total WT - OT"] = df[wt_col].apply(parse_hhmm_to_minutes) - df["OT"].apply(parse_hhmm_to_minutes)
        df["Total WT - OT"] = df["Total WT - OT"].apply(minutes_to_hhmm)
    return df

def process_sheet(raw_df):
    emp_id, emp_name, dept = parse_employee_info(raw_df)
    header_row = find_table_header_row(raw_df)
    if header_row is None:
        return None

    df = raw_df.iloc[header_row + 1:].copy()
    df.columns = rename_duplicate_columns(list(raw_df.iloc[header_row]))
    df = df.dropna(how="all").reset_index(drop=True)
    df = df.iloc[:-1]
    df = df.drop(columns=[c for c in df.columns if "Total Break" in str(c)], errors="ignore")

    df.insert(0, "Employee ID", emp_id)
    df.insert(1, "Employee Name", emp_name)
    df.insert(2, "Department Name", dept)

    return recalculate_ot(df)


CACHE_FILE = "/tmp/last_uploaded.xlsx"
CACHE_META = "/tmp/last_uploaded_name.txt"
MONTHS = ["January","February","March","April","May","June",
          "July","August","September","October","November","December"]

st.set_page_config(page_title="Salary Calculator", layout="wide")
st.title("🏢 Salary Calculator")

st.markdown("""
<style>
.section-emp {
    border-top: 2px solid #1f77b4;
    padding: 16px 0 20px 0;
    margin-bottom: 28px;
}
.section-report {
    border: 2px solid #2ca02c;
    border-radius: 10px;
    padding: 16px 20px 20px 20px;
    margin-bottom: 28px;
    background-color: #f2fff4;
}
.header-emp {
    background-color: #1f77b4;
    color: white;
    padding: 6px 14px;
    border-radius: 6px;
    font-size: 23px;
    font-weight: bold;
    display: inline-block;
    margin-bottom: 14px;
}
.header-report {
    background-color: #2ca02c;
    color: white;
    padding: 6px 14px;
    border-radius: 6px;
    font-size: 16px;
    font-weight: bold;
    display: inline-block;
    margin-bottom: 14px;
}
.payable-box {
    background-color: #fff3cd;
    border: 2px solid #ffc107;
    border-radius: 8px;
    padding: 8px 10px;
    text-align: center;
    font-size: 14px;
    font-weight: bold;
    color: #856404;
    margin-top: 4px;
}
</style>
""", unsafe_allow_html=True)

if "salary_inputs" not in st.session_state:
    st.session_state.salary_inputs = {}
if "edited_sheets" not in st.session_state:
    st.session_state.edited_sheets = {}
if "file_name" not in st.session_state:
    st.session_state.file_name = None
if "file_bytes" not in st.session_state:
    st.session_state.file_bytes = None

if st.session_state.file_bytes is None and os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, "rb") as f:
        st.session_state.file_bytes = f.read()
    if os.path.exists(CACHE_META):
        with open(CACHE_META, "r") as f:
            st.session_state.file_name = f.read().strip()
    raw_sheets = pd.read_excel(io.BytesIO(st.session_state.file_bytes), sheet_name=None, header=None)
    for sheet_name, raw_df in raw_sheets.items():
        df = process_sheet(raw_df)
        if df is not None:
            df.insert(3, "Sheet Name", sheet_name)
            st.session_state.edited_sheets[sheet_name] = df

col_file, = st.columns(1)
uploaded_file = col_file.file_uploader("Upload Excel", type=["xlsx", "xls"])
col_wd, col_month, col_year = st.columns(3)
working_days = col_wd.number_input("Total Working Days", min_value=1, step=1, value=st.session_state.get("working_days", 26))
month = col_month.selectbox("Month", MONTHS, index=st.session_state.get("month", pd.Timestamp.now().month - 1))
year = col_year.number_input("Year", min_value=2000, max_value=2100, step=1, value=st.session_state.get("year", pd.Timestamp.now().year))
st.session_state.working_days = working_days
st.session_state.month = MONTHS.index(month)
st.session_state.year = int(year)
st.markdown("---")

if uploaded_file and st.session_state.file_name != uploaded_file.name:
    st.session_state.file_name = uploaded_file.name
    st.session_state.file_bytes = uploaded_file.read()
    st.session_state.edited_sheets = {}
    st.session_state.salary_inputs = {}
    with open(CACHE_FILE, "wb") as f:
        f.write(st.session_state.file_bytes)
    with open(CACHE_META, "w") as f:
        f.write(st.session_state.file_name)
    raw_sheets = pd.read_excel(io.BytesIO(st.session_state.file_bytes), sheet_name=None, header=None)
    for sheet_name, raw_df in raw_sheets.items():
        df = process_sheet(raw_df)
        if df is not None:
            df.insert(3, "Sheet Name", sheet_name)
            st.session_state.edited_sheets[sheet_name] = df

all_dfs = []

# ── Per Employee Sections ─────────────────────────────────────────────────────
for sheet_name, df in st.session_state.edited_sheets.items():
    emp_label = df["Employee Name"].iloc[0] or sheet_name
    st.markdown(f'<div class="section-emp"><div class="header-emp">👤 {emp_label}</div>', unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)
    saved = st.session_state.salary_inputs.get(sheet_name, {"salary": 0})
    salary = col1.number_input("Monthly Salary", min_value=0, step=100, value=saved["salary"], key=f"salary_{sheet_name}")
    st.session_state.salary_inputs[sheet_name] = {"salary": salary}

    per_day = round(salary / working_days, 2) if salary > 0 and working_days > 0 else 0
    per_hour = round(per_day / 8, 2) if per_day > 0 else 0
    col2.metric("Per Day Salary", f"{per_day}")
    col3.metric("Per Hour Salary", f"{per_hour}")

    wt_col = next((c for c in df.columns if c not in ("OT", "Total WT - OT")
                   and ("WT" in str(c).upper() or "WORK" in str(c).upper())), None)
    time_cols = [c for c in df.columns if re.search(r"(?i)total\s*time", c)]
    editable_cols = {c: True for c in df.columns}
    editable_cols["OT"] = False
    editable_cols["Total WT - OT"] = False
    if wt_col:
        editable_cols[wt_col] = False
    for c in time_cols:
        editable_cols[c] = False

    edited_df = st.data_editor(
        df,
        key=f"editor_{sheet_name}",
        column_config={
            c: st.column_config.TextColumn(c, disabled=not editable_cols.get(c, True))
            for c in df.columns
        },
        use_container_width=True,
        num_rows="fixed"
    )

    recalculated_df = recalculate_ot(edited_df.copy())
    derived_cols = [c for c in ["OT", "Total WT - OT", wt_col] + time_cols if c and c in recalculated_df.columns]
    if not recalculated_df[derived_cols].equals(edited_df[derived_cols]):
        st.session_state.edited_sheets[sheet_name] = recalculated_df
        del st.session_state[f"editor_{sheet_name}"]
        st.rerun()
    edited_df = recalculated_df
    st.session_state.edited_sheets[sheet_name] = edited_df

    total_std_mins = edited_df["Total WT - OT"].apply(parse_hhmm_to_minutes).sum() if "Total WT - OT" in edited_df.columns else 0
    total_ot_mins = edited_df["OT"].apply(parse_hhmm_to_minutes).sum() if "OT" in edited_df.columns else 0
    std_amount = round((total_std_mins / 60) * per_hour, 2)
    ot_amount = round((total_ot_mins / 60) * per_hour * 1.5, 2)
    total_payable = round(std_amount + ot_amount, 2)

    col5, col6, col7, col8, col9 = st.columns(5)
    col5.metric("Total WT - OT", minutes_to_hhmm(total_std_mins))
    col6.metric("Standard Pay", f"{std_amount}")
    col7.metric("Total OT", minutes_to_hhmm(total_ot_mins))
    col8.metric("OT Pay (1.5x)", f"{ot_amount}")
    col9.markdown(f'<div class="payable-box"><span style="font-size: 1.1rem;">💰 Total Payable</span><br><span style="font-size: 1.5rem;">₹ {total_payable}</span></div>', unsafe_allow_html=True)


    st.markdown('</div>', unsafe_allow_html=True)
    all_dfs.append(edited_df)

# ── Monthly Report ────────────────────────────────────────────────────────────
if all_dfs:
    st.markdown(f'<div class="section-report"><div class="header-report">📊 Monthly Report — {month} {int(year)}</div>', unsafe_allow_html=True)
    report_rows = []
    for sheet_name, df in st.session_state.edited_sheets.items():
        saved = st.session_state.salary_inputs.get(sheet_name, {"salary": 0})
        salary = saved["salary"]
        per_day = round(salary / working_days, 2) if salary > 0 and working_days > 0 else 0
        per_hour = round(per_day / 8, 2) if per_day > 0 else 0
        total_std_mins = df["Total WT - OT"].apply(parse_hhmm_to_minutes).sum() if "Total WT - OT" in df.columns else 0
        total_ot_mins = df["OT"].apply(parse_hhmm_to_minutes).sum() if "OT" in df.columns else 0
        std_amount = round((total_std_mins / 60) * per_hour, 2)
        ot_amount = round((total_ot_mins / 60) * per_hour * 1.5, 2)
        report_rows.append({
            "Employee Name": df["Employee Name"].iloc[0],
            "Per Day Salary": per_day,
            "Per Hour Salary": per_hour,
            "Total WT - OT": minutes_to_hhmm(total_std_mins),
            "Total OT": minutes_to_hhmm(total_ot_mins),
            "Standard Pay": std_amount,
            "OT Pay": ot_amount,
            "Total Payable": round(std_amount + ot_amount, 2),
        })

    report_df = pd.DataFrame(report_rows)
    st.dataframe(report_df, use_container_width=True)
    csv = report_df.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Download Monthly Report", csv, f"report_{month}_{int(year)}.csv", "text/csv")
    st.markdown('</div>', unsafe_allow_html=True)
