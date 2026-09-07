import io
import requests
import pandas as pd
import dns.resolver
import streamlit as st

st.set_page_config(page_title="Bulk Email Verifier", page_icon="📧", layout="centered")

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
    
    all_results = {}
    batch_size = 50  # Process in chunks of 50 to avoid timeouts
    total_emails = len(emails_to_verify)
    
    # Setup live progress UI elements
    progress_text = st.empty()
    progress_bar = st.progress(0)
    
    for i in range(0, total_emails, batch_size):
        batch = emails_to_verify[i:i + batch_size]
        payload = {"emails": batch}
        
        current_batch_num = (i // batch_size) + 1
        total_batches = (total_emails + batch_size - 1) // batch_size
        progress_text.text(f"Verifying batch {current_batch_num} of {total_batches} ({min(i + batch_size, total_emails)}/{total_emails} emails)...")

        try:
            res = requests.post(url, json=payload, timeout=120)
            if res.status_code in [200, 201]:
                items = res.json()
                
                for item in items:
                    email = item.get("email") or item.get("emailAddress")
                    if not email:
                        continue
                    
                    raw_status = str(item.get("status", item.get("result", item.get("state", "")))).lower()
                    is_catch_all = item.get("is_catch_all", item.get("catchAll", False))
                    reason_text = item.get("reason", item.get("sub_status", "Server Response Logged"))
                    
                    if raw_status in ["valid", "good", "deliverable", "safe"]:
                        all_results[email] = ("VALID", reason_text)
                    elif raw_status in ["invalid", "bad", "undeliverable", "bounce", "disposable"]:
                        all_results[email] = ("INVALID", reason_text)
                    elif raw_status in ["catch_all", "catch-all", "risky", "unknown", "unconfirmed"] or is_catch_all:
                        all_results[email] = ("UNCONFIRMED", "Catch-All / Unconfirmed Server Response")
                    else:
                        all_results[email] = ("UNCONFIRMED", reason_text)
            else:
                st.warning(f"Apify batch error on items {i}-{i+batch_size}: {res.text}")
        except Exception as e:
            st.warning(f"Batch request failed for items {i}-{i+batch_size}: {e}")
        
        # Update progress bar safely
        progress_val = min(float(i + batch_size) / total_emails, 1.0)
        progress_bar.progress(progress_val)
        
    # Clear progress UI elements when complete
    progress_text.empty()
    progress_bar.empty()
    
    return all_results

uploaded_file = st.file_uploader("Upload CSV File", type=["csv"])

if uploaded_file is not None:
    try:
        df = pd.read_csv(uploaded_file)
        
        # Auto-detect email column
        email_col = next((col for col in df.columns if 'email' in col.lower()), None)
        if not email_col:
            st.error("Could not find a column named 'email'. Please check your CSV.")
            st.stop()

        if st.button("🚀 Verify Emails"):
            with st.spinner("Step 1: Running fast local DNS/MX checks..."):
                df[['local_pass', 'Verification_Status', 'Reason']] = pd.DataFrame(
                    df[email_col].apply(check_local_dns).tolist(), index=df.index
                )

            # Filter valid ones for Apify deeper check
            emails_for_apify = df.loc[df['local_pass'] == True, email_col].dropna().unique().tolist()

            if emails_for_apify:
                # Step 2 handles batching and live progress internally now
                apify_results = verify_with_apify(emails_for_apify)

                for idx, row in df.iterrows():
                    email = row[email_col]
                    if row['local_pass'] and email in apify_results:
                        status, reason = apify_results[email]
                        df.at[idx, 'Verification_Status'] = status
                        df.at[idx, 'Reason'] = reason

            df.drop(columns=['local_pass'], inplace=True)

            st.success("Verification Complete!")
            
            # Show summary metrics
            valid_count = len(df[df['Verification_Status'] == 'VALID'])
            invalid_count = len(df[df['Verification_Status'] == 'INVALID'])
            unconfirmed_count = len(df[df['Verification_Status'] == 'UNCONFIRMED'])
            
            col1, col2, col3 = st.columns(3)
            col1.metric("✅ Valid", valid_count)
            col2.metric("❌ Invalid", invalid_count)
            col3.metric("⚠️ Unconfirmed", unconfirmed_count)

            st.dataframe(df)

            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Cleaned CSV",
                data=csv,
                file_name="bounceverify_cleaned_emails.csv",
                mime="text/csv",
            )
            
    except Exception as e:
        st.error(f"Error processing CSV: {e}")
