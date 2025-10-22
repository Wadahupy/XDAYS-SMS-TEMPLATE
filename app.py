import re
import io
import zipfile
from typing import List, Dict

import pandas as pd
import streamlit as st


TARGET_HEADERS = [
	"CYCLE",
	"AC",
	"Customer Number",
	"Mobile Number",
	"Network",
	"OB",
	"MPR",
	"PDA",
	"DUEDATE",
	"PTP",
	"TEMPLATE",
]

TEMPLATES = [
	"Cutoff SMS",
	"MPR (3 days bef cut off)",
	"MPR/PDA (last 2 days bef cutoff)",
	"After due date",
	"TPAP 2",
	"BP SMS – after dd and cut off date",
	"PTP Reminder",
	"Uncontacted Accounts",
	"PTP SMS ( Promise to Pay SMS)",
]


def normalize(col: str) -> str:
	if col is None:
		return ""
	return re.sub(r"[^a-z0-9]", "", str(col).lower())


def detect_cycle_from_filename(filename: str) -> str:
	# Examples: BPI_XDAYS_C21_HEADER_ALIGNED_10-21-2025 (1).csv -> 21
	if not filename:
		return ""
	# Look for C<number>
	m = re.search(r"\bC(\d{1,4})\b", filename, flags=re.IGNORECASE)
	if m:
		return m.group(1)
	# Fallback: look for _C<number>_
	m = re.search(r"_C(\d{1,4})[_\-]", filename, flags=re.IGNORECASE)
	if m:
		return m.group(1)
	# last resort: look for _<number>_ before HEADER
	m = re.search(r"_(\d{1,4})_HEADER", filename, flags=re.IGNORECASE)
	if m:
		return m.group(1)
	return ""


def map_and_align_columns(df: pd.DataFrame, target_headers: List[str]) -> pd.DataFrame:
	# Create mapping from normalized existing columns to original
	existing = list(df.columns)
	normalized_map: Dict[str, str] = {normalize(c): c for c in existing}

	# Build new dataframe with target headers in order
	out = pd.DataFrame()
	for th in target_headers:
		n = normalize(th)
		if n in normalized_map:
			out[th] = df[normalized_map[n]]
		else:
			# try fuzzy: find any column that contains the token(s)
			candidates = [c for c in existing if n in normalize(c) or normalize(c) in n]
			if candidates:
				out[th] = df[candidates[0]]
			else:
				# missing column -> fill with NaN
				out[th] = pd.NA

	return out


def make_zip_of_templates(df: pd.DataFrame, templates: List[str], target_headers: List[str]) -> bytes:
	buf = io.BytesIO()
	with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
		for t in templates:
			# Filter rows where TEMPLATE matches template name (case-insensitive)
			if "TEMPLATE" in df.columns:
				mask = df["TEMPLATE"].astype(str).str.strip().str.lower() == t.lower()
				filtered = df[mask]
			else:
				filtered = df.iloc[0:0]

			# Ensure header alignment on each filtered file
			aligned = map_and_align_columns(filtered, target_headers)
			csv_bytes = aligned.to_csv(index=False).encode("utf-8")
			safe_name = re.sub(r"[^A-Za-z0-9 _.-]", "", t)
			z.writestr(f"{safe_name}.csv", csv_bytes)

	return buf.getvalue()


def main():
	st.set_page_config(page_title="SMS Blast XDAYS - Header Align", layout="wide")
	st.title("SMS Blast XDAYS — Header Alignment & Template Filters")

	st.markdown("Upload a CSV or Excel file. The app will detect the cycle from the filename and align headers to the expected format.")

	uploaded = st.file_uploader("Upload CSV / Excel file", type=["csv", "xls", "xlsx"], accept_multiple_files=False)

	if not uploaded:
		st.info("No file uploaded yet. Please upload a CSV or Excel file.")
		return

	filename = getattr(uploaded, "name", "uploaded_file")
	detected_cycle = detect_cycle_from_filename(filename)
	st.write(f"Detected cycle from filename: **{detected_cycle or 'not found'}**")

	# Read file into DataFrame
	try:
		if filename.lower().endswith(".csv"):
			df = pd.read_csv(uploaded, dtype=str)
		else:
			df = pd.read_excel(uploaded, dtype=str)
	except Exception as e:
		st.error(f"Failed to read file: {e}")
		return

	st.write(f"Original columns ({len(df.columns)}): {list(df.columns)}")

	# Ensure CYCLE column exists and fill if missing
	if "CYCLE" not in df.columns:
		df["CYCLE"] = detected_cycle or pd.NA

	aligned = map_and_align_columns(df, TARGET_HEADERS)

	st.subheader("Preview of aligned headers (first 10 rows)")
	st.dataframe(aligned.head(10))

	# Prepare header-aligned CSV for download
	aligned_csv = aligned.to_csv(index=False).encode("utf-8")
	safe_fname = re.sub(r"[^A-Za-z0-9_.-]", "_", filename)
	out_name = f"{safe_fname.rsplit('.',1)[0]}_HEADER_ALIGNED.csv"

	col1, col2 = st.columns(2)
	with col1:
		st.download_button("Download header-aligned file", data=aligned_csv, file_name=out_name, mime="text/csv")

	# Prepare zip of per-template CSVs
	zip_bytes = make_zip_of_templates(aligned, TEMPLATES, TARGET_HEADERS)
	with col2:
		st.download_button("Download per-template CSVs (zip)", data=zip_bytes, file_name=f"{safe_fname.rsplit('.',1)[0]}_TEMPLATES.zip", mime="application/zip")

	# Show counts per template
	st.subheader("Counts per template (from aligned data)")
	if "TEMPLATE" in aligned.columns:
		counts = aligned["TEMPLATE"].fillna("(blank)").astype(str).value_counts()
		st.table(counts)
	else:
		st.info("No TEMPLATE column found in aligned data.")


if __name__ == "__main__":
	main()

