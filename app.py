import io
import requests
import pandas as pd
import dns.resolver
import streamlit as st

st.set_page_config(page_title="Bulk Email Verifier", page_icon="📧", layout="centered")

st.title("📧 Bulk Email Verifier")
st.write("Upload your CSV file to clean your email list.")

# Pull API Token securely from Streamlit Secrets
APIFY_TOKEN = st.secrets.get("APIFY_TOKEN", "")

def check_local_dns(email):
    """Fast local check for syntax and MX records."""
    if not isinstance(email, str) or not email.strip() or '@' not in email:
        return False, "INVALID (Format)"
    
    domain = email.strip().split('@')[-1]
    try:
        dns.resolver.resolve(domain, 'MX')
        return True, "VALID_MX"
    except Exception:
        return False, "INVALID (No MX Record)"

def verify_with_apify(emails_to_verify):
    """Sends candidate emails to Apify for deep SMTP verification."""
    if not APIFY_TOKEN:
        st.error("Missing APIFY_TOKEN in Streamlit Secrets!")
        return {}

    url = f"https://api.apify.com/v2/acts/reacher~email-verifier/run-sync-get-dataset-items?token={APIFY_TOKEN}"
    payload = {"emails": emails_to_verify}

    try:
        res = requests.post(url, json=payload, timeout=120)
        if res.status_code in [200, 201]:
            items = res.json()
            # Map email -> verification result
            results = {}
            for item in items:
                email = item.get("email")
                is_valid = item.get("is_valid")
                is_catch_all = item.get("is_catch_all", False)
                
                if is_valid:
                    results[email] = "VALID"
                elif is_catch_all:
                    results[email] = "RISKY (Catch-All)"
                else:
                    results[email] = "INVALID"
            return results
    except Exception as e:
        st.warning(f"Apify call failed: {e}")
    
    return {}

uploaded_file = st.file_uploader("Drop your CSV file here", type=["csv"])

if uploaded_file is not None:
    df = pd.read_csv(uploaded_file)
    st.success(f"Loaded {len(df)} rows.")

    email_col = st.selectbox("Select Email Column", df.columns, index=0)

    if st.button("Start Verification", type="primary"):
        status_text = st.empty()
        status_text.text("Step 1/2: Filtering invalid domains locally...")

        emails = df[email_col].astype(str).str.strip().tolist()
        final_statuses = []
        apify_candidates = []

        # Local DNS pre-check
        for email in emails:
            has_mx, reason = check_local_dns(email)
            if has_mx:
                apify_candidates.append(email)
                final_statuses.append("PENDING")
            else:
                final_statuses.append(reason)

        # Apify deep check for remaining emails
        if apify_candidates:
            status_text.text(f"Step 2/2: Verifying {len(apify_candidates)} active domains with Apify...")
            apify_results = verify_with_apify(apify_candidates)

            # Merge results
            for i, email in enumerate(emails):
                if final_statuses[i] == "PENDING":
                    final_statuses[i] = apify_results.get(email, "RISKY (Unconfirmed)")

        # Clean existing status columns
        for col in ['Validity', 'Verification_Status', 'Verification_Details', 'Status']:
            if col in df.columns:
                df.drop(columns=[col], inplace=True)

        df['Status'] = final_statuses

        status_text.empty()
        st.success("Verification Complete!")
        st.dataframe(df)

        csv_buffer = io.BytesIO()
        df.to_csv(csv_buffer, index=False)
        csv_buffer.seek(0)

        st.download_button(
            label="📥 Download Cleaned CSV",
            data=csv_buffer,
            file_name=f"verified_{uploaded_file.name}",
            mime="text/csv"
        )
