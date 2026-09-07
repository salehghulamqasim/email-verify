import io
import requests
import pandas as pd
import dns.resolver
import streamlit as st

st.set_page_config(page_title="Bulk Email Verifier", page_icon="📧", layout="centered")

# --- GLOBAL RESET BUTTON ---
with st.sidebar:
    st.header("Controls")
    if st.button("🔄 Reset App"):
        st.session_state.clear()
        st.rerun()
    st.caption("Click to clear all results and start over if the app gets stuck.")

st.title("📧 Bulk Email Verifier")
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
                
                raw_status = str(item.get("status", item.get("result", item.get("state", "")))).lower()
                is_catch_all = item.get("is_catch_all", item.get("catchAll", False))
                reason_text = item.get("reason", item.get("sub_status", "Server Response Logged"))
                
                if raw_status in ["valid", "good", "deliverable", "safe"]:
                    results[email] = ("VALID", reason_text)
                elif raw_status in ["invalid", "bad", "undeliverable", "bounce", "disposable"]:
                    results[email] = ("INVALID", reason_text)
                elif raw_status in ["catch_all", "catch-all", "risky", "unknown", "unconfirmed"] or is_catch_all:
                    results[email] = ("UNCONFIRMED", "Catch-All / Unconfirmed Server Response")
                else:
                    results[email] = ("UNCONFIRMED", reason_text)
                    
            return results
        else:
            st.error(f"Apify Error: {res.text}")
            return {}
    except Exception as e:
        st.error(f"Request failed: {e}")
        return {}

uploaded_file = st.file_uploader("Upload CSV File", type=["csv"])

if uploaded_file is not None:
    try:
        # Read CSV exactly as is
        df = pd.read_csv(uploaded_file)
        
        email_col = next((col for col in df.columns if 'email' in col.lower()), None)
        if not email_col:
            st.error("Could not find a column named 'email'. Please check your CSV.")
            st.stop()

        # Initialize session state for storing results
        if 'verified_df' not in st.session_state:
            st.session_state.verified_df = None

        if st.button("🚀 Verify Emails"):
            with st.spinner("Step 1: Running fast local DNS/MX checks..."):
                # Clean whitespace only for the check, without altering original CSV data
                clean_emails = df[email_col].astype(str).str.strip()
                
                df[['local_pass', 'Verification_Status', 'Reason']] = pd.DataFrame(
                    clean_emails.apply(check_local_dns).tolist(), index=df.index
                )

            # Filter valid ones for Apify deeper check using the cleaned emails
            emails_for_apify = clean_emails[df['local_pass'] == True].dropna().unique().tolist()

            if emails_for_apify:
                with st.spinner(f"Step 2: Performing Deep SMTP/Catch-all check for {len(emails_for_apify)} emails using BounceVerify..."):
                    apify_results = verify_with_apify(emails_for_apify)

                    for idx, row in df.iterrows():
                        email = str(row[email_col]).strip()
                        if row['local_pass'] and email in apify_results:
                            status, reason = apify_results[email]
                            df.at[idx, 'Verification_Status'] = status
                            df.at[idx, 'Reason'] = reason

            # Drop the temporary processing column so it doesn't appear in the final CSV
            df.drop(columns=['local_pass'], inplace=True)

            # Save the fully intact dataframe to session state
            st.session_state.verified_df = df
            st.success("Verification Complete!")
            
        # Display metrics and download button
        if st.session_state.verified_df is not None:
            final_df = st.session_state.verified_df
            
            valid_count = len(final_df[final_df['Verification_Status'] == 'VALID'])
            invalid_count = len(final_df[final_df['Verification_Status'] == 'INVALID'])
            unconfirmed_count = len(final_df[final_df['Verification_Status'] == 'UNCONFIRMED'])
            
            col1, col2, col3 = st.columns(3)
            col1.metric("✅ Valid", valid_count)
            col2.metric("❌ Invalid", invalid_count)
            col3.metric("⚠️ Unconfirmed", unconfirmed_count)

            st.dataframe(final_df)

            csv = final_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Verified CSV",
                data=csv,
                file_name="bounceverify_results.csv",
                mime="text/csv",
            )
            
    except Exception as e:
        st.error(f"Error processing CSV: {e}")
