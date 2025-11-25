import re
import io
import zipfile
from typing import List, Dict
from datetime import datetime
import pandas as pd
import streamlit as st

# =========================================================
# CONFIGURATION
# =========================================================

# 1️⃣ Define main target headers first
TARGET_HEADERS = [
    "CYCLE",
    "LOAN NUMBER",
    "CUSTOMER NAME",
    "MOBILE NUMBER",
    "BOS/CB",
    "OB",
    "MPR",
    "PDA",
    "PTP AMT",
    "PTP DATE",
    "TPAP DD",
    "BUCKET",
    "WITH CONTACT Y/N",
    "SMS TEMPLATE",
    "PREVIEW"
]

# 2️⃣ Export headers (no PREVIEW)
EXPORT_HEADERS = [h for h in TARGET_HEADERS if h != "PREVIEW"]

# 3️⃣ Header aliases for flexible mapping
HEADER_ALIASES = {
    "CYCLE": ["COLLECTION CYCLE", "CYCLES"],
    "LOAN NUMBER": ["LOAN NO", "LOAN#", "loan number", "ACCOUNT NUMBER", "ACCOUNT NO", "ACCT NO", "ACCT NUMBER"],
    "CUSTOMER NAME": ["CLIENT NAME", "BORROWER NAME", "CUSTOMER", "NAME"],
    "MOBILE NUMBER": ["CONTACT NO", "CONTACT NUMBER", "CELLPHONE NO", "PHONE NUMBER", "MOBILE NO"],
    "BOS/CB": ["BOS", "CB", "BRANCH", "CENTER"],
    "OB": ["Amount Overdue", "OUTSTANDING BALANCE", "AMOUNT_OUTSTANDING", "OUTSTANDING", "OVERDUE AMOUNT"],
    "MPR": ["MONTHLY PAYMENT RATE", "MPR VALUE", "MAD"],
    "PDA": ["XDAYS", "XDAYS_AMOUNT", "pda"],
    "PTP AMT": ["PTP AMOUNT", "PROMISE TO PAY AMT", "AMOUNT PROMISED"],
    "PTP DATE": ["PROMISE DATE", "PROMISED DATE", "PTP SCHEDULE"],
    "TPAP DD": ["TPAP DATE", "TPA DATE", "TPAP SCHED", "TPA DD"],
    "BUCKET": ["AGING BUCKET", "AGE", "BUCKET CATEGORY"],
    "WITH CONTACT Y/N": ["WITH CONTACT", "CONTACTABLE", "HAS CONTACT"],
    "SMS TEMPLATE": ["TEMPLATE", "MESSAGE TYPE", "SMS TYPE"],
    "PREVIEW": ["MESSAGE PREVIEW", "TEXT PREVIEW"]
}

EXPORT_HEADERS = [h for h in TARGET_HEADERS if h != "PREVIEW"]

# SMS Template Dictionary
TEMPLATES = {
    "CUT OFF SMS": st.secrets["templates"]["CUT_OFF_SMS"],
    "MPR (3DYS) CUTOFF SMS": st.secrets["templates"]["MPR_3D_CUTOFF_SMS"],
    "MPR-PDA (L2DYS) CUTOFF SMS": st.secrets["templates"]["MPR_PDA_L2DY_CUTOFF_SMS"],
    "AFTER DUE DATE SMS": st.secrets["templates"]["AFTER_DUE_DATE_SMS"],
    "TPAP SMS": st.secrets["templates"]["TPAP_SMS"],
    "BP SMS (MPR-PDA)": st.secrets["templates"]["BP_SMS_MPR_PDA"],
    "BP SMS NOT DUE (AOD-MPR)": st.secrets["templates"]["BP_SMS_NOT_DUE_AOD_MPR"],
    "AOD-MPR": st.secrets["templates"]["AOD_MPR"],
    "PTP REMINDER SMS": st.secrets["templates"]["PTP_REMINDER_SMS"],
    "PTP SMS": st.secrets["templates"]["PTP_SMS"],
    "UNCONTACTED SMS": st.secrets["templates"]["UNCONTACTED_SMS"],
    "PAYDAY SMS": st.secrets["templates"]["PAYDAY_SMS"],
    "PRE PAYDAY SMS": st.secrets["templates"]["PRE_PAYDAY_SMS"],
    "INSUFF SMS (MPR-PDA)": st.secrets["templates"]["INSUFF_SMS_MPR_PDA"],
    "INSUFF SMS NOT DUE (AOD-MPR)": st.secrets["templates"]["INSUFF_SMS_NOT_DUE_AOD_MPR"],
}


# =========================================================
# UTILITY FUNCTIONS
# =========================================================
def normalize(col: str) -> str:
    """Normalize header for fuzzy matching."""
    if col is None:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(col).lower())


def detect_cycle_from_filename(filename: str) -> str:
    """Extract cycle number (e.g., C14) from filename."""
    if not filename:
        return ""
    m = re.search(r"c(\d{1,3})", filename, flags=re.IGNORECASE)
    return m.group(1) if m else ""


def map_and_align_columns(df: pd.DataFrame, target_headers: List[str]) -> pd.DataFrame:
    """
    Align columns based on fuzzy/alias header matches and include a 'DETECTION NAME' column
    showing which raw header was used for each aligned header.
    """
    existing = list(df.columns)
    normalized_existing = {normalize(c): c for c in existing}

    out = pd.DataFrame()
    detection_info = {}

    for target in target_headers:
        n_target = normalize(target)
        detected_col = None

        # 1️⃣ Direct match
        if n_target in normalized_existing:
            detected_col = normalized_existing[n_target]
            out[target] = df[detected_col]

        # 2️⃣ Try aliases (with normalization)
        elif target in HEADER_ALIASES:
            for alias in HEADER_ALIASES[target]:
                n_alias = normalize(alias)
                match = next((normalized_existing[c] for c in normalized_existing if n_alias == c), None)
                if match:
                    detected_col = match
                    out[target] = df[detected_col]
                    break

        # 3️⃣ Try fuzzy match (partial match)
        if detected_col is None:
            for c in existing:
                if n_target in normalize(c) or normalize(c) in n_target:
                    detected_col = c
                    out[target] = df[c]
                    break

        # 4️⃣ If still not found, fill with NA
        if detected_col is None:
            out[target] = pd.NA

        detection_info[target] = detected_col if detected_col else "(Not Found)"

    # --- Create Detection Table ---
    detection_df = pd.DataFrame({
        "HEADER": list(detection_info.keys()),
        "DETECTION NAME": list(detection_info.values())
    })

    with st.expander("🧭 Column Detection Reference", expanded=False):
        st.dataframe(detection_df, use_container_width=True)

    return out


def calculate_bucket(ob_value):
    """Compute bucket category based on OB value safely."""
    try:
        val = float(str(ob_value).replace(",", "").strip())
        if pd.isna(val):
            return pd.NA
        if val >= 100000:
            return "100K and up"
        elif val >= 50000:
            return "50K - 99K"
        elif val >= 6000:
            return "6K - 49K"
        elif val >= 0:
            return "0 - 5K"
        else:
            return pd.NA
    except Exception:
        return pd.NA


def format_excel_text_date(value) -> str:
    """Normalize various date representations into MM/DD/YYYY (no time).

    Handles:
    - datetime objects
    - common date/time strings (with or without time)
    - Excel serial numbers (as int/float or digit-strings)
    - returns empty string for missing values
    """
    if pd.isna(value):
        return ""

    s = str(value).strip()
    if s == "":
        return ""

    # Try direct pandas parsing first
    try:
        # If value looks numeric (possible Excel serial), try origin conversion
        if re.fullmatch(r"\d+(?:\.0+)?", s):
            try:
                num = float(s)
                # treat as excel serial when value is reasonably large
                if num > 31:
                    dt = pd.to_datetime(num, unit="D", origin="1899-12-30", errors="coerce")
                    if not pd.isna(dt):
                        return dt.strftime("%m/%d/%Y")
            except Exception:
                pass

        # Generic parse (works for many string formats and datetime objects)
        dt = pd.to_datetime(s, errors="coerce", infer_datetime_format=True)
        if not pd.isna(dt):
            return dt.strftime("%m/%d/%Y")

        # Fallback: try parsing only the first token (strip time)
        token = s.split()[0]
        dt = pd.to_datetime(token, errors="coerce", infer_datetime_format=True)
        if not pd.isna(dt):
            return dt.strftime("%m/%d/%Y")
    except Exception:
        pass

    # Last resort: return the date-like portion without time
    return s.split()[0]


def process_data(df: pd.DataFrame, cycle: str) -> pd.DataFrame:
    """Complete all transformations and computed fields."""
    result = map_and_align_columns(df, TARGET_HEADERS)

    # --- Format Excel text-style dates ---
    for date_col in ["PTP DATE", "TPAP DD"]:
        if date_col in result.columns:
            result[date_col] = result[date_col].apply(format_excel_text_date)

    # --- Continue normal flow ---
    result["CYCLE"] = cycle
    result["BUCKET"] = result["OB"].apply(calculate_bucket)
    result["WITH CONTACT Y/N"] = result["MOBILE NUMBER"].apply(check_contact)

    # ✅ Keep SMS TEMPLATE from raw file if already exists
    if "SMS TEMPLATE" not in df.columns:
        result["SMS TEMPLATE"] = result.apply(detect_template, axis=1)

    # --- Always generate preview based on the detected or existing template
    result["PREVIEW"] = result.apply(
        lambda r: format_preview(TEMPLATES.get(r["SMS TEMPLATE"], ""), r),
        axis=1
    )

    return result
    # --- Reorder columns: insert BUCKET right after TPAP DD ---
    if "TPAP DD" in result.columns and "BUCKET" in result.columns:
        cols = list(result.columns)
        tpap_index = cols.index("TPAP DD")
        # Remove and reinsert BUCKET right after TPAP DD
        cols.remove("BUCKET")
        cols.insert(tpap_index + 1, "BUCKET")
        result = result[cols]

    return result


def with_contact_flag(mobile):
    if not isinstance(mobile, str):
        return "N"
    mobile = mobile.strip()
    if re.fullmatch(r"63\d{10}$", mobile):
        return "Y"
    return "N"



def check_contact(mobile: str) -> str:
    """Validate contact number format."""
    if pd.isna(mobile):
        return "N"
    m = str(mobile).strip()
    return "Y" if re.match(r"^63\d{10}$", m) else "N"


def detect_template(row):
    # Safely extract numeric fields
    ob_val = row.get("OB", 0)
    mpr_val = row.get("MPR", 0)
    pda_val = row.get("PDA", 0)

    try:
        ob = float(ob_val) if pd.notna(ob_val) and str(ob_val).strip() != "" else 0.0
        mpr = float(mpr_val) if pd.notna(mpr_val) and str(mpr_val).strip() != "" else 0.0
        pda = float(pda_val) if pd.notna(pda_val) and str(pda_val).strip() != "" else 0.0
    except ValueError:
        ob, mpr, pda = 0.0, 0.0, 0.0

    # Example logic (adjust yours here)
    if mpr > 0 and pda == 0:
        return "WITH MPR ONLY"
    elif pda > 0 and mpr == 0:
        return "WITH PDA ONLY"
    elif mpr > 0 and pda > 0:
        return "WITH BOTH MPR AND PDA"
    else:
        return "NO PAYMENT"


def format_preview(template, row):
    """Fill in SMS template placeholders using row values, including formatted TPAP DD."""

    def safe_float(val):
        try:
            if pd.isna(val) or str(val).strip() in ["", "nan", "None"]:
                return 0.0
            return float(val)
        except Exception:
            return 0.0

    # --- Safe replacements ---
    replacements = {
        "{CUST_NAME}": str(row.get("CUSTOMER NAME", "")),
        "{ACC_NO}": str(row.get("LOAN NUMBER", "")),  # fixed to use LOAN NUMBER, not ACCOUNT NUMBER
        "{OB}": f"{safe_float(row.get('OB', 0)):,.2f}",
        "{MPR}": f"{safe_float(row.get('MPR', 0)):,.2f}",
        "{PDA}": f"{safe_float(row.get('PDA', 0)):,.2f}",
        "{TPAP DD}": str(row.get("TPAP DD", "")),     # ✅ Add this line
        "{PTP DATE}": str(row.get("PTP DATE", "")),   # optional: in case template uses it
        "{CYCLE}": str(row.get("CYCLE", "")),         # corrected from COLLECTION_CYCLE
    }

    # --- Apply all replacements ---
    for key, value in replacements.items():
        template = template.replace(key, value)

    return template


def xlookup_pda(main_df: pd.DataFrame, pda_df: pd.DataFrame) -> pd.DataFrame:
    """Perform PDA merge (XLOOKUP style)."""
    lookup = pda_df.set_index("LOAN NUMBER")["PDA"].to_dict()
    main_df["PDA"] = main_df["LOAN NUMBER"].map(lambda x: lookup.get(str(x).strip(), pd.NA))
    return main_df


def process_data(df: pd.DataFrame, cycle: str) -> pd.DataFrame:
    """Complete all transformations and computed fields."""
    result = map_and_align_columns(df, TARGET_HEADERS)
    # --- Normalize date fields to MM/DD/YYYY (no time)
    for date_col in ["PTP DATE", "TPAP DD"]:
        if date_col in result.columns:
            result[date_col] = result[date_col].apply(format_excel_text_date)

    result["CYCLE"] = cycle
    result["BUCKET"] = result["OB"].apply(calculate_bucket)
    result["WITH CONTACT Y/N"] = result["MOBILE NUMBER"].apply(check_contact)
    result["PREVIEW"] = result.apply(
        lambda r: format_preview(TEMPLATES.get(r["SMS TEMPLATE"], ""), r), axis=1
    )

    return result

def to_excel_bytes(df: pd.DataFrame) -> bytes:
    """Convert DataFrame to downloadable Excel file in memory."""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="SMS Data")
    return output.getvalue()


# =========================================================
# STREAMLIT APP
# =========================================================
def main():
    st.set_page_config(page_title="📲 SMS Blast XDAYS", layout="wide")
    st.title("📱 SMS Blast XDAYS")

    tabs = st.tabs(["Step 1️⃣ Upload & Align", "Step 2️⃣ Review & Export"])

    # --- Step 1
    with tabs[0]:
        st.header("Step 1: Upload Primary File")
        st.markdown("""
        Upload your **raw SMS file** (CSV or Excel).  
        This will:
        - Detect the cycle number from filename  
        - Align all headers  
        - Compute `BUCKET`, `WITH CONTACT Y/N`, and choose SMS Template  
        - Generate a `PREVIEW` of messages
        """)

        uploaded_main = st.file_uploader("📂 Upload Primary File", type=["csv", "xls", "xlsx"])
        if uploaded_main:
            fname = uploaded_main.name
            cycle = detect_cycle_from_filename(fname)
            st.info(f"Detected CYCLE: **C{cycle or 'N/A'}**")

            try:
                # --- Read uploaded file ---
                if fname.lower().endswith(".csv"):
                    df = pd.read_csv(uploaded_main, dtype=str, na_filter=False)
                else:
                    df = pd.read_excel(uploaded_main, dtype=str, na_filter=False)

                # --- Clean numeric fields (OB, MPR, PDA) before processing ---
                for col in ["OB", "MPR", "PDA"]:
                    if col in df.columns:
                        df[col] = (
                            df[col]
                            .astype(str)
                            .str.replace(",", "", regex=False)
                            .str.strip()
                            .replace({"": "0", "nan": "0", "None": "0"})
                        )

                # --- Process file ---
                processed = process_data(df, cycle)

                # --- Display results ---
                st.dataframe(processed.head(10), use_container_width=True)
                st.session_state["processed_main"] = processed
                st.session_state["main_filename"] = fname
                st.success("✅ Primary file processed successfully!")

            except Exception as e:
                import traceback
                st.error(f"❌ Error: {e}")
                st.text_area("Debug Traceback", traceback.format_exc(), height=200)


    # --- Step 3
    with tabs[1]:
        st.header("Step 2: Review & Export Final Output")

        if "processed_main" not in st.session_state:
            st.warning("Please finish Steps 1 & 2 first.")
        else:
            df = st.session_state["processed_main"]
            st.dataframe(df, use_container_width=True)

            st.markdown("### 📊 Summary")
            col1, col2, col3 = st.columns(3)
            col1.metric("Total Records", len(df))
            col2.metric("Contactable", (df["WITH CONTACT Y/N"] == "Y").sum())
            col3.metric("Unique Templates", df["SMS TEMPLATE"].nunique())

            st.bar_chart(df["SMS TEMPLATE"].value_counts())

         # --- Downloads
            st.markdown("### 💾 Download Files")

            csv_full = df.to_csv(index=False).encode("utf-8")
            csv_export = df[EXPORT_HEADERS].to_csv(index=False).encode("utf-8")

            st.download_button(
                "📥 Full CSV (with Preview)",
                csv_full,
                file_name=f"SMS_FULL_{st.session_state['main_filename']}.csv"
            )
            
            st.download_button(
            "📥 Full EXCEL (with Preview)",
            csv_full,
            file_name=f"SMS_FULL_{st.session_state['main_filename']}.csv"
            )



if __name__ == "__main__":
    main()
