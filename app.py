import io
import socket
import smtplib
import pandas as pd
import dns.resolver
import dns.exception
import streamlit as st
from concurrent.futures import ThreadPoolExecutor

st.set_page_config(page_title="Bulk Email Verifier", page_icon="📧", layout="centered")

st.title("📧 Bulk Email Verifier")
st.write("Upload your CSV file to clean your email list before sending cold outreach.")

TIMEOUT = 5
MAX_WORKERS = 10

def check_single_email(email):
    if not isinstance(email, str) or not email.strip() or '@' not in email:
        return "INVALID_FORMAT", "Missing '@' or empty address"

    email = email.strip()
    domain = email.split('@')[-1]

    try:
        mx_records = dns.resolver.resolve(domain, 'MX')
        primary_mx = str(sorted(mx_records, key=lambda r: r.preference)[0].exchange).rstrip('.')
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
        return "INVALID", "Domain does not exist or has no active MX records"
    except Exception as e:
        return "DNS_ERROR", str(e)

    try:
        server = smtplib.SMTP(timeout=TIMEOUT)
        server.connect(primary_mx, 25)
        server.helo(socket.gethostname())
        server.mail("verify@example.com")
        code, response = server.rcpt(email)
        server.quit()

        if code == 250:
            return "VALID", "250 OK"
        elif code in [550, 551, 552, 553, 554]:
            return "INVALID", f"{code} Mailbox does not exist"
        else:
            return "CATCH_ALL/RISKY", f"Code {code}"
    except socket.timeout:
        return "TIMEOUT", "SMTP Connection timed out"
    except Exception as e:
        return "ERROR", str(e)

uploaded_file = st.file_uploader("Drop your CSV file here", type=["csv"])

if uploaded_file is not None:
    df = pd.read_csv(uploaded_file)
    st.success(f"Loaded {len(df)} rows.")

    email_col = st.selectbox("Select Email Column", df.columns, index=0)

    if st.button("Start Verification", type="primary"):
        progress_bar = st.progress(0)
        status_text = st.empty()

        emails = df[email_col].astype(str).tolist()
        results = []

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            for i, res in enumerate(executor.map(check_single_email, emails)):
                results.append(res)
                progress = (i + 1) / len(emails)
                progress_bar.progress(progress)
                status_text.text(f"Verifying {i+1}/{len(emails)}: {emails[i]}")

        df['Verification_Status'] = [r[0] for r in results]
        df['Verification_Details'] = [r[1] for r in results]

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
