import os
import json
import logging
import threading
import time
import requests
import sys
from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore

# --- App & Logging Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout)
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# --- Firebase Admin SDK Initialization ---
db = None
try:
    service_account_str = os.getenv('FIREBASE_SERVICE_ACCOUNT_JSON')
    if not service_account_str:
        logging.warning("FIREBASE_SERVICE_ACCOUNT_JSON env var not set. Pinger will not run.")
    else:
        service_account_info = json.loads(service_account_str)
        cred = credentials.Certificate(service_account_info)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        logging.info("Firebase Admin SDK initialized successfully.")
except Exception as e:
    logging.error(f"Error initializing Firebase Admin SDK: {e}")

# --- Pinger Background Thread ---
def pinger_worker():
    logging.info("Pinger worker thread started.")
    while True:
        if not db:
            time.sleep(60)
            continue
        
        try:
            monitors_to_check = []
            users_ref = db.collection('users')
            for user in users_ref.stream():
                monitors_ref = user.reference.collection('monitors')
                for monitor in monitors_ref.stream():
                    monitor_data = monitor.to_dict()
                    last_checked = monitor_data.get('lastChecked', firestore.SERVER_TIMESTAMP)
                    if hasattr(last_checked, 'timestamp'):
                        last_checked = last_checked.timestamp()
                    interval = int(monitor_data.get('interval', 60))
                    if time.time() - last_checked > interval:
                        monitors_to_check.append(monitor)
            
            for monitor in monitors_to_check:
                monitor_data = monitor.to_dict()
                url = monitor_data.get('url')
                if not url: continue
                status = 'down'
                try:
                    headers = {'User-Agent': 'SitePulse/1.0 (+https://your-frontend-url.netlify.app)'}
                    response = requests.get(url, timeout=10, headers=headers, allow_redirects=True)
                    if response.ok:
                        status = 'up'
                except requests.RequestException:
                    status = 'down'
                
                monitor.reference.update({
                    'status': status,
                    'lastChecked': firestore.SERVER_TIMESTAMP
                })
                logging.info(f"Pinged {url}. Status: {status}")
        except Exception as e:
            logging.error(f"An error occurred in the pinger loop: {e}", exc_info=True)
            
        time.sleep(5)

if db:
    pinger_thread = threading.Thread(target=pinger_worker, daemon=True)
    pinger_thread.start()

# --- API Routes ---
@app.route('/')
def health_check():
    return jsonify({"status": "ok", "message": "SitePulse Backend is running!"})

@app.route('/monitors', methods=['POST'])
def add_monitor():
    if not db: return jsonify({"error": "Database service not available."}), 503
    try:
        data = request.json
        uid, url, name = data.get('uid'), data.get('url'), data.get('name')
        interval = data.get('interval', 60)
        if not all([uid, url, name]):
            return jsonify({"error": "Missing required fields."}), 400
        monitor_ref = db.collection('users').document(uid).collection('monitors').document()
        monitor_ref.set({
            'name': name, 'url': url, 'interval': int(interval),
            'status': 'pending', 'createdAt': firestore.SERVER_TIMESTAMP,
            'lastChecked': 0
        })
        return jsonify({"message": "Monitor added successfully!", "id": monitor_ref.id}), 201
    except Exception as e:
        logging.error(f"Error adding monitor: {e}", exc_info=True)
        return jsonify({"error": "Failed to add monitor."}), 500

@app.route('/monitors/<monitor_id>', methods=['DELETE'])
def delete_monitor(monitor_id):
    if not db: return jsonify({"error": "Database service not available."}), 503
    try:
        uid = request.json.get('uid')
        if not uid: return jsonify({"error": "User ID is required."}), 400
        monitor_ref = db.collection('users').document(uid).collection('monitors').document(monitor_id)
        monitor_ref.delete()
        return jsonify({"message": "Monitor deleted successfully."}), 200
    except Exception as e:
        logging.error(f"Error deleting monitor {monitor_id}: {e}", exc_info=True)
        return jsonify({"error": "Failed to delete monitor."}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

