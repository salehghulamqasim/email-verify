import io
import requests
import pandas as pd
import dns.resolver
import streamlit as st

st.set_page_config(page_title="Bulk Email Verifier", page_icon="📧", layout="centered")

st.title("📧 Bulk Email Verifier - URXPRT team")
st.write("Upload your CSV file to verify emails and get clean results.")

APIFY_TOKEN = st.secrets.get("APIFY_TOKEN", "")

def check_local_dns(email):
    if not isinstance(email, str) or not email.strip() or '@' not in email:
        return False, "INVALID", "Invalid Email Format"
    
    domain = email.strip().split('@')[-1]
    try:
        dns.resolver.resolve(domain, 'MX')
        return True, "PENDING", "Valid MX"
    except Exception:
        return False, "INVALID", "Domain or MX Record Missing"

def verify_with_apify(emails_to_verify):
    if not APIFY_TOKEN:
        st.error("Missing APIFY_TOKEN in Streamlit Secrets!")
        return {}

    url = f"https://api.apify.com/v2/acts/reacher~email-verifier/run-sync-get-dataset-items?token={APIFY_TOKEN}"
    payload = {"emails": emails_to_verify}

    try:
        res = requests.post(url, json=payload, timeout=120)
        if res.status_code in [200, 201]:
            items = res.json()
            results = {}
            for item in items:
                email = item.get("email")
                is_valid = item.get("is_valid")
                is_catch_all = item.get("is_catch_all", False)
                
                if is_valid:
                    results[email] = ("CONFIRMED", "Mailbox Deliverable")
                elif is_catch_all:
                    results[email] = ("UNCONFIRMED", "Catch-All Server Configured")
                else:
                    results[email] = ("INVALID", "Mailbox Undeliverable")
            return results
    except Exception as e:
        st.warning(f"Apify check failed: {e}")
    
    return {}

uploaded_file = st.file_uploader("Drop your CSV file here", type=["csv"])

if uploaded_file is not None:
    df = pd.read_csv(uploaded_file)
    st.success(f"Loaded {len(df)} rows.")

    default_index = 0
    for idx, col in enumerate(df.columns):
        if 'email' in col.lower() or 'بريد' in col.lower():
            default_index = idx
            break

    email_col = st.selectbox("Select Email Column", df.columns, index=default_index)

    if st.button("Start Verification", type="primary"):
        status_text = st.empty()
        status_text.text("Step 1/2: Pre-filtering invalid domains locally...")

        emails = df[email_col].astype(str).str.strip().tolist()
        statuses = []
        reasons = []
        apify_candidates = []

        for email in emails:
            has_mx, status, reason = check_local_dns(email)
            if has_mx:
                apify_candidates.append(email)
                statuses.append("PENDING")
                reasons.append("Pending Apify Check")
            else:
                statuses.append(status)
                reasons.append(reason)

        if apify_candidates:
            status_text.text(f"Step 2/2: Verifying {len(apify_candidates)} active domains with Apify...")
            apify_results = verify_with_apify(apify_candidates)

            for i, email in enumerate(emails):
                if statuses[i] == "PENDING":
                    res_status, res_reason = apify_results.get(email, ("UNCONFIRMED", "Unconfirmed Server Response"))
                    statuses[i] = res_status
                    reasons[i] = res_reason

        # Output ONLY Email, Status, and Reason
        clean_df = pd.DataFrame({
            'Email': emails,
            'Status': statuses,
            'Reason': reasons
        })

        status_text.empty()
        st.success("Verification Complete!")
        st.dataframe(clean_df)

        csv_buffer = io.BytesIO()
        clean_df.to_csv(csv_buffer, index=False)
        csv_buffer.seek(0)

        st.download_button(
            label="📥 Download Cleaned CSV",
            data=csv_buffer,
            file_name=f"verified_{uploaded_file.name}",
            mime="text/csv"
        )

# ---------------------------------------------------------
# Team Guidance & Caution Notice at the bottom of the page
# ---------------------------------------------------------
st.divider()
st.subheader("💡 Important Notice & Team Guidance")

st.markdown("""
* **Focus on `CONFIRMED` and `INVALID` Data:**
  * **CONFIRMED:** These addresses are active, deliverable, and safe to copy directly into your email outreach tools.
  * **INVALID:** These addresses are confirmed dead or incorrectly formatted. Exclude them immediately to protect our sender domain reputation.

* **How to Handle `UNCONFIRMED`:**
  * Do not remove uncofirmed emails. do add them in file but with unconfirmed label.
  
* **A Big Step Closer, Not 100% Absolute:**
  * Automated email verification is a high-precision filtering tool, but **no verification platform on the market is 100% infallible**. Mail server security policies, temporary greylisting, and internal firewalls can evolve dynamically over time. Use these results as a strong pre-send shield rather than a 100% guarantee.
""")
