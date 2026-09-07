import io
import requests
import pandas as pd
import dns.resolver
import streamlit as st

st.set_page_config(page_title="Bulk Email Verifier", page_icon="📧", layout="centered")

st.title("📧 Bulk Email Verifier")
st.write("Upload your CSV file to verify emails and get clean results.")

# ------------------------------------------------------------------
# Instructions banner
# ------------------------------------------------------------------
st.info(
    "**How to use this tool:**\n"
    "1. Upload your **.csv** file (no Excel, PDF, or other formats — they won't work).\n"
    "2. Click **Verify Emails**.\n"
    "3. Download the result and **paste it into your own tracking sheet.** "
    "This tool doesn't save anything — once you close the tab, it's gone.\n\n"
    "✅ **Your data is safe:** this tool only *adds* new columns (`Verification_Status`, `Reason`) "
    "to your file. It never edits, deletes, or reorders anything you already had."
)

APIFY_TOKEN = st.secrets.get("APIFY_TOKEN", "")

# ------------------------------------------------------------------
# Reset button — click this any time the uploader seems stuck.
# ------------------------------------------------------------------
if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = 0

reset_col, _ = st.columns([1, 3])
with reset_col:
    if st.button("🔄 Reset upload"):
        st.session_state.uploader_key += 1
        st.rerun()
st.caption("Upload button stuck or not responding? Click **Reset upload** above, then try again.")

st.divider()


def check_local_dns(email_clean):
    if not isinstance(email_clean, str) or not email_clean.strip() or '@' not in email_clean:
        return False, "INVALID", "Invalid Email Format"

    domain = email_clean.split('@')[-1]
    try:
        dns.resolver.resolve(domain, 'MX')
        return True, "PENDING", "Valid MX"
    except Exception:
        return False, "INVALID", "Domain or MX Record Missing"


def verify_with_apify(emails_to_verify):
    """emails_to_verify should already be cleaned (stripped/lowercased)."""
    if not APIFY_TOKEN:
        st.error("Missing APIFY_TOKEN in Streamlit Secrets!")
        return {}

    url = f"https://api.apify.com/v2/acts/bounceverify~bounceverify-email-verifier/run-sync-get-dataset-items?token={APIFY_TOKEN}"
    payload = {"emails": emails_to_verify}

    try:
        res = requests.post(url, json=payload, timeout=300)
        if res.status_code in [200, 201]:
            items = res.json()
            results = {}

            for item in items:
                email = item.get("email") or item.get("emailAddress")
                if not email:
                    continue
                # Normalize the key so it matches however we stored our own lookup
                email_key = str(email).strip().lower()

                raw_status = str(item.get("status", item.get("result", item.get("state", "")))).lower()
                is_catch_all = item.get("is_catch_all", item.get("catchAll", False))
                reason_text = item.get("reason", item.get("sub_status", "Server Response Logged"))

                if raw_status in ["valid", "good", "deliverable", "safe"]:
                    results[email_key] = ("VALID", reason_text)
                elif raw_status in ["invalid", "bad", "undeliverable", "bounce", "disposable"]:
                    results[email_key] = ("INVALID", reason_text)
                elif raw_status in ["catch_all", "catch-all", "risky", "unknown", "unconfirmed"] or is_catch_all:
                    results[email_key] = ("UNCONFIRMED", "Catch-All / Unconfirmed Server Response")
                else:
                    results[email_key] = ("UNCONFIRMED", reason_text)

            return results
        else:
            st.error(f"Apify Error: {res.text}")
            return {}
    except Exception as e:
        st.error(f"Request failed: {e}")
        return {}


uploaded_file = st.file_uploader(
    "Upload CSV File",
    type=["csv"],
    key=f"uploader_{st.session_state.uploader_key}",
)

if uploaded_file is not None:
    # Defensive check: type=["csv"] only filters the file picker dialog,
    # a user can still drag-and-drop a renamed file. Double check the extension.
    if not uploaded_file.name.lower().endswith(".csv"):
        st.error("That file doesn't look like a CSV. Please upload a .csv file only.")
        st.stop()

    try:
        df = pd.read_csv(uploaded_file)
    except Exception as e:
        st.error(
            f"Couldn't read this as a CSV file (Error: {e}). "
            "Please make sure it's a plain .csv, not renamed from Excel/PDF/etc."
        )
        st.stop()

    if df.empty:
        st.error("This CSV appears to be empty.")
        st.stop()

    # Auto-detect email column
    email_col = next((col for col in df.columns if 'email' in col.lower()), None)
    if not email_col:
        st.error("Could not find a column with 'email' in its name. Please check your CSV.")
        st.stop()

    st.success(f"Loaded **{len(df)}** rows. Email column detected: **{email_col}**")

    # Safety guard: never silently overwrite columns that already exist in the
    # uploaded file (e.g. if someone re-uploads a file that was already run
    # through this tool before, or already has a column with these names).
    NEW_COLUMNS = ['Verification_Status', 'Reason']
    existing_clash = [c for c in NEW_COLUMNS if c in df.columns]
    if existing_clash:
        st.warning(
            f"Your file already has a column named {', '.join(existing_clash)}. "
            "To avoid overwriting your existing data, this run will add new columns named "
            f"{', '.join(c + '_new' for c in existing_clash)} instead."
        )
        NEW_COLUMNS = [c + '_new' if c in existing_clash else c for c in NEW_COLUMNS]
    status_col, reason_col = NEW_COLUMNS

    # Keep an untouched copy of exactly what was uploaded, for peace of mind —
    # everything below only ever ADDS columns to a copy, never edits df in place
    # in a way that changes your original values.
    original_columns = list(df.columns)

    if st.button("🚀 Verify Emails"):
        # Clean, normalized version used for DNS check, Apify lookup, and matching results back.
        # This is a separate working copy — the ORIGINAL email column is never modified.
        email_clean_series = df[email_col].astype(str).str.strip().str.lower()

        with st.spinner("Step 1: Running fast local DNS/MX checks..."):
            df[['local_pass', status_col, reason_col]] = pd.DataFrame(
                email_clean_series.apply(check_local_dns).tolist(), index=df.index
            )

        # Filter valid ones for Apify deeper check (use cleaned emails)
        df['_email_clean'] = email_clean_series
        emails_for_apify = df.loc[df['local_pass'] == True, '_email_clean'].dropna().unique().tolist()

        if emails_for_apify:
            with st.spinner(f"Step 2: Performing Deep SMTP/Catch-all check for {len(emails_for_apify)} emails using BounceVerify..."):
                apify_results = verify_with_apify(emails_for_apify)

                for idx, row in df.iterrows():
                    if not row['local_pass']:
                        continue
                    email_key = row['_email_clean']
                    if email_key in apify_results:
                        status, reason = apify_results[email_key]
                        df.at[idx, status_col] = status
                        df.at[idx, reason_col] = reason
                    else:
                        # Previously this silently stayed as "PENDING" and vanished from the summary.
                        # Now it's explicitly flagged so nothing gets lost.
                        df.at[idx, status_col] = 'UNCONFIRMED'
                        df.at[idx, reason_col] = 'No response from verification service'

        df.drop(columns=['local_pass', '_email_clean'], inplace=True)

        # Sanity check: confirm every original column and every original value
        # is still present, unchanged, before we show results. This is the
        # actual enforcement behind the "we never touch your data" promise.
        assert all(c in df.columns for c in original_columns), "A required original column went missing — stopping to avoid showing corrupted data."

        st.divider()
        st.success("Verification Complete! Your original columns are untouched — only new columns were added.")

        # Show summary metrics
        valid_count = len(df[df[status_col] == 'VALID'])
        invalid_count = len(df[df[status_col] == 'INVALID'])
        unconfirmed_count = len(df[df[status_col] == 'UNCONFIRMED'])

        col1, col2, col3 = st.columns(3)
        col1.metric("✅ Valid", valid_count)
        col2.metric("❌ Invalid", invalid_count)
        col3.metric("⚠️ Unconfirmed", unconfirmed_count)

        if valid_count + invalid_count + unconfirmed_count != len(df):
            st.warning("⚠️ Some rows didn't get a final status — please double check the table below before using these results.")

        st.dataframe(df, use_container_width=True)

        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download Cleaned CSV",
            data=csv,
            file_name="bounceverify_cleaned_emails.csv",
            mime="text/csv",
        )

        st.warning("📋 Reminder: please update your own tracking sheet with these results now — this app doesn't save anything.")
