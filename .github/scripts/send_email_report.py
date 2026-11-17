#!/usr/bin/env python3
"""
Test Jig CSV Report Emailer
Dispatches the latest test results from the production database to the designated recipients via SMTP.
"""

import os
import sys
import json
import csv
import io
import smtplib
import urllib.request
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

# ==============================================================================
# CONFIGURATION & RECIPIENTS
# ==============================================================================

RECIPIENTS = ["ravil.indiesemic@gmail.com", "dharmveer@indiesemic.com"]

API_ENDPOINT = "https://biqnkyuwdgyuzlgfmtmp.supabase.co/rest/v1/jig_test_results?select=*&order=id.desc"
API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJpcW5reXV3ZGd5dXpsZ2ZtdG1wIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTM4NzcyNjEsImV4cCI6MjEwOTQ1MzI2MX0.6aY9E4raqTKfLlmPQyVxijwcfh_yWH-4942SkKcEW-A"

def fetch_data():
    """Fetches all test results from the backend API, sorted latest first."""
    req = urllib.request.Request(API_ENDPOINT, headers={
        "apikey": API_KEY,
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status != 200:
            raise RuntimeError(f"API returned status {resp.status}")
        return json.loads(resp.read().decode("utf-8"))

def generate_csv(records):
    """Converts records list to CSV text."""
    if not records:
        return ""
    
    headers = [
        "ID", "SR", "Jig ID", "Timestamp", "MAC", "FICR MAC",
        "RSSI (dBm)", "BLE", "IMU Acc Test", "Mic Result",
        "LED Delta (mA)", "Amber LED Delta (mA)", "Active Stream (mA)",
        "V Charging (V)", "Sleep Current (mA)", "Client Firmware",
        "Result", "Comments", "Created At"
    ]
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    
    for r in records:
        writer.writerow([
            r.get("id", ""),
            r.get("sr", ""),
            r.get("jig_id", ""),
            r.get("timestamp", ""),
            r.get("mac", ""),
            r.get("ficr_mac", ""),
            r.get("rssi_dbm", ""),
            r.get("ble", ""),
            r.get("imu_acctest", ""),
            r.get("mic_result", ""),
            r.get("led_delta_ma", ""),
            r.get("amber_led_delta_ma", ""),
            r.get("active_stream_ma", ""),
            r.get("V_charging", ""),
            r.get("sleep_current_ma", ""),
            r.get("client_firmware", ""),
            r.get("result", ""),
            r.get("comments", ""),
            r.get("created_at", "")
        ])
    
    return output.getvalue()

def calculate_stats(records):
    """Calculates overall and per-jig statistics."""
    stats = {
        "overall": {"total": len(records), "success": 0, "failure": 0},
        "jigs": {
            1: {"total": 0, "success": 0, "failure": 0},
            2: {"total": 0, "success": 0, "failure": 0},
            3: {"total": 0, "success": 0, "failure": 0},
        }
    }
    
    for r in records:
        res = str(r.get("result", "")).strip().upper()
        is_pass = (res == "PASS" or res == "SUCCESS")
        
        if is_pass:
            stats["overall"]["success"] += 1
        else:
            stats["overall"]["failure"] += 1
            
        jig_str = str(r.get("jig_id", "")).strip().lower()
        jig_num = None
        if "1" in jig_str:
            jig_num = 1
        elif "2" in jig_str:
            jig_num = 2
        elif "3" in jig_str:
            jig_num = 3
            
        if jig_num and jig_num in stats["jigs"]:
            stats["jigs"][jig_num]["total"] += 1
            if is_pass:
                stats["jigs"][jig_num]["success"] += 1
            else:
                stats["jigs"][jig_num]["failure"] += 1
                
    return stats

def send_email(csv_content, stats):
    """Sends the email with CSV attached using SMTP credentials from environment variables."""
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASSWORD")
    smtp_from = os.environ.get("SMTP_FROM", smtp_user)
    
    if not (smtp_host and smtp_user and smtp_pass):
        raise ValueError("Missing SMTP configuration. Set SMTP_HOST, SMTP_USER, and SMTP_PASSWORD secrets.")
    
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    date_tag = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    
    msg = MIMEMultipart("mixed")
    msg["From"] = smtp_from
    msg["To"] = ", ".join(RECIPIENTS)
    msg["Subject"] = f"Test Jig Production Data Export ({now_str})"
    
    # Body Summary
    total = stats["overall"]["total"]
    succ = stats["overall"]["success"]
    fail = stats["overall"]["failure"]
    yield_pct = f"{(succ / total * 100):.1f}%" if total > 0 else "0.0%"
    
    j1 = stats["jigs"][1]
    j2 = stats["jigs"][2]
    j3 = stats["jigs"][3]
    
    body_text = f"""Test Jig Production Report
Generated at: {now_str}

OVERALL METRICS:
------------------------------------
Total Tested:  {total}
Total Success: {succ}
Total Failure: {fail}
Overall Yield: {yield_pct}

JIG METRICS:
------------------------------------
Jig 1:
  Total Count: {j1['total']}
  Success:     {j1['success']}
  Failures:    {j1['failure']}

Jig 2:
  Total Count: {j2['total']}
  Success:     {j2['success']}
  Failures:    {j2['failure']}

Jig 3:
  Total Count: {j3['total']}
  Success:     {j3['success']}
  Failures:    {j3['failure']}

The complete CSV dataset has been attached to this email.
"""
    
    body_html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #1e293b; background-color: #f8fafc; padding: 20px;">
        <div style="max-width: 600px; margin: auto; background: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; padding: 24px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);">
          <h2 style="color: #0f172a; margin-top: 0; border-bottom: 2px solid #3b82f6; padding-bottom: 8px;">
            Test Jig Production Report
          </h2>
          <p style="font-size: 13px; color: #64748b;">Report Generated: <strong>{now_str}</strong></p>
          
          <h3 style="color: #1e293b; margin-top: 20px;">Overall Metrics</h3>
          <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px; font-size: 14px;">
            <tr style="background-color: #f1f5f9;">
              <th style="padding: 10px; border: 1px solid #cbd5e1; text-align: left;">Total Tested</th>
              <th style="padding: 10px; border: 1px solid #cbd5e1; text-align: left; color: #16a34a;">Total Success</th>
              <th style="padding: 10px; border: 1px solid #cbd5e1; text-align: left; color: #dc2626;">Total Failure</th>
              <th style="padding: 10px; border: 1px solid #cbd5e1; text-align: left;">Yield</th>
            </tr>
            <tr>
              <td style="padding: 10px; border: 1px solid #cbd5e1; font-weight: bold;">{total}</td>
              <td style="padding: 10px; border: 1px solid #cbd5e1; font-weight: bold; color: #16a34a;">{succ}</td>
              <td style="padding: 10px; border: 1px solid #cbd5e1; font-weight: bold; color: #dc2626;">{fail}</td>
              <td style="padding: 10px; border: 1px solid #cbd5e1; font-weight: bold;">{yield_pct}</td>
            </tr>
          </table>

          <h3 style="color: #1e293b;">Jig Performance Containers</h3>
          <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
            <thead>
              <tr style="background-color: #0f172a; color: #ffffff;">
                <th style="padding: 10px; border: 1px solid #334155; text-align: left;">Jig Station</th>
                <th style="padding: 10px; border: 1px solid #334155; text-align: center;">Total Count</th>
                <th style="padding: 10px; border: 1px solid #334155; text-align: center; color: #4ade80;">Success</th>
                <th style="padding: 10px; border: 1px solid #334155; text-align: center; color: #f87171;">Failures</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td style="padding: 10px; border: 1px solid #cbd5e1; font-weight: bold;">Jig 1</td>
                <td style="padding: 10px; border: 1px solid #cbd5e1; text-align: center;">{j1['total']}</td>
                <td style="padding: 10px; border: 1px solid #cbd5e1; text-align: center; color: #16a34a; font-weight: bold;">{j1['success']}</td>
                <td style="padding: 10px; border: 1px solid #cbd5e1; text-align: center; color: #dc2626; font-weight: bold;">{j1['failure']}</td>
              </tr>
              <tr style="background-color: #f8fafc;">
                <td style="padding: 10px; border: 1px solid #cbd5e1; font-weight: bold;">Jig 2</td>
                <td style="padding: 10px; border: 1px solid #cbd5e1; text-align: center;">{j2['total']}</td>
                <td style="padding: 10px; border: 1px solid #cbd5e1; text-align: center; color: #16a34a; font-weight: bold;">{j2['success']}</td>
                <td style="padding: 10px; border: 1px solid #cbd5e1; text-align: center; color: #dc2626; font-weight: bold;">{j2['failure']}</td>
              </tr>
              <tr>
                <td style="padding: 10px; border: 1px solid #cbd5e1; font-weight: bold;">Jig 3</td>
                <td style="padding: 10px; border: 1px solid #cbd5e1; text-align: center;">{j3['total']}</td>
                <td style="padding: 10px; border: 1px solid #cbd5e1; text-align: center; color: #16a34a; font-weight: bold;">{j3['success']}</td>
                <td style="padding: 10px; border: 1px solid #cbd5e1; text-align: center; color: #dc2626; font-weight: bold;">{j3['failure']}</td>
              </tr>
            </tbody>
          </table>
          
          <p style="margin-top: 24px; font-size: 13px; color: #64748b;">
            Attached: <code>test_jig_results_{date_tag}.csv</code> containing all raw records with newest entries first.
          </p>
        </div>
      </body>
    </html>
    """
    
    msg_alt = MIMEMultipart("alternative")
    msg_alt.attach(MIMEText(body_text, "plain"))
    msg_alt.attach(MIMEText(body_html, "html"))
    msg.attach(msg_alt)
    
    # Attach CSV
    part = MIMEBase("application", "octet-stream")
    part.set_payload(csv_content.encode("utf-8"))
    encoders.encode_base64(part)
    filename = f"test_jig_results_{date_tag}.csv"
    part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
    msg.attach(part)
    
    print(f"Connecting to SMTP server {smtp_host}:{smtp_port}...")
    if smtp_port == 465:
        server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30)
    else:
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
        server.ehlo()
        server.starttls()
        server.ehlo()
        
    server.login(smtp_user, smtp_pass)
    print(f"Sending email to: {', '.join(RECIPIENTS)}...")
    server.sendmail(smtp_from, RECIPIENTS, msg.as_string())
    server.quit()
    print("Email sent successfully!")

def main():
    print("Fetching test jig records...")
    records = fetch_data()
    print(f"Successfully fetched {len(records)} records.")
    
    stats = calculate_stats(records)
    print(f"Overall: Total={stats['overall']['total']}, Success={stats['overall']['success']}, Failures={stats['overall']['failure']}")
    
    print("Generating CSV...")
    csv_content = generate_csv(records)
    
    print("Sending email...")
    send_email(csv_content, stats)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
