import time
from functools import lru_cache

import requests
import pandas as pd
import dns.resolver
import streamlit as st

st.set_page_config(page_title="Bulk Email Verifier", page_icon="📧", layout="centered")

st.title("📧 Bulk Email Verifier")
st.write("Upload your CSV file to verify emails and get clean results.")

APIFY_TOKEN = st.secrets.get("APIFY_TOKEN", "")
ACTOR_ID = "bounceverify~bounceverify-email-verifier"

# ---------------- Settings (tweakable in the sidebar) ----------------
with st.sidebar:
    st.header("Settings")
    BATCH_SIZE = st.number_input("Emails per Apify run", min_value=20, max_value=500, value=100, step=10)
    MAX_RETRIES = st.number_input("Retries per batch", min_value=1, max_value=5, value=3, step=1)
    POLL_INTERVAL = st.number_input("Poll interval (sec)", min_value=2, max_value=30, value=5, step=1)
    MAX_WAIT_PER_BATCH = st.number_input("Max wait per batch (sec)", min_value=60, max_value=3600, value=1200, step=60)

# ---------------- Local DNS/MX check (cached per domain) ----------------
@lru_cache(maxsize=8192)
def _domain_has_mx(domain):
    try:
        dns.resolver.resolve(domain, "MX")
        return True
    except Exception:
        return False


def check_local_dns(email):
    if not isinstance(email, str) or not email.strip() or "@" not in email:
        return False, "INVALID", "Invalid Email Format"
    domain = email.strip().split("@")[-1].lower()
    if _domain_has_mx(domain):
        return True, "PENDING", "Valid MX"
    return False, "INVALID", "Domain or MX Record Missing"


# ---------------- Apify: async run + poll (no more 120s read timeouts) ----------------
def _start_run(batch):
    url = f"https://api.apify.com/v2/acts/{ACTOR_ID}/runs?token={APIFY_TOKEN}"
    res = requests.post(url, json={"emails": batch}, timeout=30)
    res.raise_for_status()
    return res.json()["data"]["id"]


def _wait_for_run(run_id):
    url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={APIFY_TOKEN}"
    waited = 0
    while waited < MAX_WAIT_PER_BATCH:
        res = requests.get(url, timeout=30)
        res.raise_for_status()
        data = res.json()["data"]
        if data["status"] in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            return data
        time.sleep(POLL_INTERVAL)
        waited += POLL_INTERVAL
    return {"status": "TIMED-OUT", "defaultDatasetId": None}


def _fetch_dataset_items(dataset_id):
    items = []
    limit = 1000
    offset = 0
    while True:
        url = (
            f"https://api.apify.com/v2/datasets/{dataset_id}/items"
            f"?token={APIFY_TOKEN}&format=json&limit={limit}&offset={offset}"
        )
        res = requests.get(url, timeout=60)
        res.raise_for_status()
        chunk = res.json()
        items.extend(chunk)
        if len(chunk) < limit:
            break
        offset += limit
    return items


def _classify(item):
    email = item.get("email") or item.get("emailAddress")
    if not email:
        return None, None, None
    raw_status = str(item.get("status", item.get("result", item.get("state", "")))).lower()
    is_catch_all = item.get("is_catch_all", item.get("catchAll", False))
    reason_text = item.get("reason", item.get("sub_status", "Server Response Logged"))

    if raw_status in ["valid", "good", "deliverable", "safe"]:
        return email, "VALID", reason_text
    if raw_status in ["invalid", "bad", "undeliverable", "bounce", "disposable"]:
        return email, "INVALID", reason_text
    if raw_status in ["catch_all", "catch-all", "risky", "unknown", "unconfirmed"] or is_catch_all:
        return email, "UNCONFIRMED", "Catch-All / Unconfirmed Server Response"
    return email, "UNCONFIRMED", reason_text


def _run_one_batch(batch, batch_num, total_batches, done_count, total, progress_text):
    """Runs a single batch through Apify with retries. Returns dict {normalized_email: (status, reason)}."""
    results = {}
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            progress_text.text(
                f"Batch {batch_num}/{total_batches}: starting run (attempt {attempt}/{MAX_RETRIES})..."
            )
            run_id = _start_run(batch)

            progress_text.text(
                f"Batch {batch_num}/{total_batches}: verifying {len(batch)} emails "
                f"({done_count}/{total})..."
            )
            run_data = _wait_for_run(run_id)

            if run_data["status"] != "SUCCEEDED":
                raise RuntimeError(f"run ended with status {run_data['status']}")

            dataset_id = run_data.get("defaultDatasetId")
            items = _fetch_dataset_items(dataset_id) if dataset_id else []

            for item in items:
                email, status, reason = _classify(item)
                if email:
                    results[email.strip().lower()] = (status, reason)
            return results

        except Exception as e:
            if attempt == MAX_RETRIES:
                st.warning(f"Batch {batch_num} failed after {MAX_RETRIES} attempts: {e}")
            else:
                time.sleep(3 * attempt)
    return results


def verify_with_apify(emails_to_verify):
    if not APIFY_TOKEN:
        st.error("Missing APIFY_TOKEN in Streamlit Secrets!")
        return {}

    # Dedupe by normalized email so we don't pay to verify the same address twice
    seen = {}
    for e in emails_to_verify:
        key = e.strip().lower()
        if key not in seen:
            seen[key] = e
    unique_emails = list(seen.values())

    all_results = {}
    total = len(unique_emails)
    total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

    progress_text = st.empty()
    progress_bar = st.progress(0)

    for i in range(0, total, BATCH_SIZE):
        batch = unique_emails[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        done_count = min(i + BATCH_SIZE, total)

        batch_results = _run_one_batch(batch, batch_num, total_batches, done_count, total, progress_text)
        all_results.update(batch_results)

        progress_bar.progress(done_count / total)

    # Second pass: retry anything still missing, in smaller batches (higher success chance)
    leftover = [e for e in unique_emails if e.strip().lower() not in all_results]
    if leftover:
        progress_text.text(f"Retrying {len(leftover)} unresolved emails in smaller batches...")
        small_batch = max(20, min(BATCH_SIZE, 50))
        for i in range(0, len(leftover), small_batch):
            batch = leftover[i : i + small_batch]
            batch_results = _run_one_batch(batch, 1, 1, i + len(batch), len(leftover), progress_text)
            all_results.update(batch_results)

    progress_text.empty()
    progress_bar.empty()
    return all_results


# ---------------- Main app flow (session_state so results survive the download click) ----------------
if "results_df" not in st.session_state:
    st.session_state.results_df = None
if "uploaded_name" not in st.session_state:
    st.session_state.uploaded_name = None

uploaded_file = st.file_uploader("Upload CSV File", type=["csv"])

if uploaded_file is not None:
    # Reset previous results if a new file is uploaded
    if st.session_state.uploaded_name != uploaded_file.name:
        st.session_state.results_df = None
        st.session_state.uploaded_name = uploaded_file.name

    try:
        df_preview = pd.read_csv(uploaded_file)
        email_col = next((col for col in df_preview.columns if "email" in col.lower()), None)
        if not email_col:
            st.error("Could not find a column named 'email'. Please check your CSV.")
            st.stop()

        st.write(f"Found **{len(df_preview)}** rows. Email column: **{email_col}**")

        if st.button("🚀 Verify Emails"):
            df = df_preview.copy()

            with st.spinner("Step 1: Running fast local DNS/MX checks..."):
                df[["local_pass", "Verification_Status", "Reason"]] = pd.DataFrame(
                    df[email_col].apply(check_local_dns).tolist(), index=df.index
                )

            emails_for_apify = df.loc[df["local_pass"] == True, email_col].dropna().tolist()

            if emails_for_apify:
                apify_results = verify_with_apify(emails_for_apify)

                for idx, row in df.iterrows():
                    if not row["local_pass"]:
                        continue
                    key = str(row[email_col]).strip().lower()
                    if key in apify_results:
                        status, reason = apify_results[key]
                        df.at[idx, "Verification_Status"] = status
                        df.at[idx, "Reason"] = reason
                    else:
                        # Still pending after every retry = the service never returned a result
                        df.at[idx, "Verification_Status"] = "ERROR"
                        df.at[idx, "Reason"] = "No response from verification service after retries"

            df.drop(columns=["local_pass"], inplace=True)
            st.session_state.results_df = df

    except Exception as e:
        st.error(f"Error processing CSV: {e}")

if st.session_state.results_df is not None:
    df = st.session_state.results_df

    st.success("Verification Complete!")

    valid_count = int((df["Verification_Status"] == "VALID").sum())
    invalid_count = int((df["Verification_Status"] == "INVALID").sum())
    unconfirmed_count = int((df["Verification_Status"] == "UNCONFIRMED").sum())
    error_count = int((df["Verification_Status"] == "ERROR").sum())

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("✅ Valid", valid_count)
    col2.metric("❌ Invalid", invalid_count)
    col3.metric("⚠️ Unconfirmed", unconfirmed_count)
    col4.metric("🚫 Error", error_count)

    st.dataframe(df)

    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="📥 Download Cleaned CSV",
        data=csv,
        file_name="bounceverify_cleaned_emails.csv",
        mime="text/csv",
    )

    if st.button("🔄 Start Over"):
        st.session_state.results_df = None
        st.rerun()
