import nltk
from nltk.tokenize import word_tokenize
from flask import Flask, render_template, request, jsonify, session
from flask_session import Session
from flask_cors import CORS
import json
import os
import sqlite3
import requests
import random
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from rapidfuzz import fuzz

import matplotlib
matplotlib.use('Agg') # Essential for server environments
import matplotlib.pyplot as plt
import pandas as pd
import io
from datetime import datetime

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = 'your_bot_token_on_telegrame'  
TELEGRAM_CHAT_ID = 'telegram_bot_id'       
DATABASE = os.path.join(os.path.dirname(__file__), 'chatbot.db')

app = Flask(__name__)
CORS(app)
app.secret_key = 'your_secret_key_here'
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_FILE_DIR'] = os.path.join(os.path.dirname(__file__), 'sessions')

# Fix: Create session directory if it doesn't exist
os.makedirs(app.config['SESSION_FILE_DIR'], exist_ok=True)

Session(app)


# --- DATABASE INITIALIZATION ---
def init_db():
    with sqlite3.connect(DATABASE) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS enquiries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT NOT NULL,
            course TEXT NOT NULL
        )''')
       
        conn.execute('''CREATE TABLE IF NOT EXISTS callbacks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            preferred_time TEXT NOT NULL
        )''')
       
        conn.execute('''CREATE TABLE IF NOT EXISTS queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_query TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')
        conn.commit()

init_db()

# --- TELEGRAM FUNCTIONS ---
def send_telegram_alert(message_body):
    """Sends a text message to Telegram"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message_body,
            "parse_mode": "Markdown"
        }
        requests.post(url, json=payload)
        print("✅ Telegram alert sent!")
    except Exception as e:
        print(f"❌ Telegram Error: {e}")

def generate_and_send_telegram_report():
    """Generates the bar chart and sends it to Telegram"""
    try:
        # 1. Fetch Data
        conn = sqlite3.connect(DATABASE)
        df = pd.read_sql_query("SELECT user_query FROM queries", conn)
        conn.close()

        if df.empty:
            return "No data available to generate report."

        # 2. Analyze
        top_queries = df['user_query'].str.lower().value_counts().head(5)
        
        # 3. Plot
        plt.figure(figsize=(10, 6))
        colors = ['#4F46E5', '#6366F1', '#818CF8', '#A5B4FC', '#C7D2FE']
        ax = top_queries.plot(kind='bar', color=colors, edgecolor='black', alpha=0.8)
        
        plt.title('Top 5 Most Asked Questions', fontsize=16, fontweight='bold', pad=20)
        plt.xlabel('Question Topic', fontsize=12)
        plt.ylabel('Frequency', fontsize=12)
        plt.xticks(rotation=45, ha='right')
        plt.grid(axis='y', linestyle='--', alpha=0.3)
        plt.tight_layout()

        # 4. Save to buffer
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        buf.seek(0)
        plt.close()

        # 5. Send to Telegram
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        files = {'photo': ('report.png', buf, 'image/png')}
        caption = f"📊 *Live Analytics Report*\n\nTotal Queries Processed: {len(df)}\nTop Trend: *{top_queries.index[0].title()}*"
        
        data = {
            'chat_id': TELEGRAM_CHAT_ID, 
            'caption': caption, 
            'parse_mode': 'Markdown'
        }
        
        requests.post(url, files=files, data=data)
        return "Report sent to Telegram successfully!"

    except Exception as e:
        print(f"Reporting Error: {e}")
        return f"Error: {str(e)}"

# --- NLP SETUP ---
try:
    nltk.data.find("tokenizers/punkt")
except LookupError:
    nltk.download("punkt")

FAQS = []
faq_path = os.path.join(os.path.dirname(__file__), "admission_faq.json")
try:
    with open(faq_path, "r", encoding="utf-8") as f:
        FAQS = json.load(f)
    print(f"✅ Loaded {len(FAQS)} FAQs from database.")
except Exception as e:
    print(f"❌ Error loading FAQs: {e}")

small_talk = {}
small_talk_path = os.path.join(os.path.dirname(__file__), "small_talk.json")
try:
    with open(small_talk_path, "r", encoding="utf-8") as f:
        small_talk = json.load(f)
except Exception as e:
    print(f"⚠️ Error loading Small Talk: {e}")

questions_list = []
answers_map = {} 

def preprocess_text(text):
    if not isinstance(text, str): return ""
    return text.lower().strip()

for faq in FAQS:
    if faq.get('question_en'):
        questions_list.append(faq['question_en'])
        answers_map[len(questions_list)-1] = faq
    elif faq.get('question'):
        questions_list.append(faq['question'])
        answers_map[len(questions_list)-1] = faq
    
    if faq.get('question_hi'):
        questions_list.append(faq['question_hi'])
        answers_map[len(questions_list)-1] = faq

vectorizer = TfidfVectorizer(stop_words='english')
tfidf_matrix = None

if questions_list:
    try:
        tfidf_matrix = vectorizer.fit_transform(questions_list)
        print("✅ Search Engine Initialized Successfully")
    except ValueError:
        print("⚠️ Warning: Empty vocabulary.")

# --- HELPER FUNCTIONS ---

def handle_eligibility_flow(question):
    """Handles the eligibility checker flow"""
    step = session.get('eligibility_step')
    
    if step == 'ask_stream':
        session['eligibility_stream'] = question.upper()
        session['eligibility_step'] = 'ask_percentage'
        return jsonify({
            "answer": "What is your 12th percentage?",
            "suggestions": []
        })
    
    elif step == 'ask_percentage':
        try:
            # Clean percentage input
            clean_input = question.replace('%', '').strip()
            percentage = float(clean_input)
            session['eligibility_percentage'] = percentage
            session['eligibility_step'] = 'ask_category'
            return jsonify({
                "answer": "What is your category (General/OBC/SC/ST)?",
                "suggestions": []
            })
        except ValueError:
            return jsonify({
                "answer": "Please enter a valid percentage number (e.g. 75).",
                "suggestions": []
            })
    
    elif step == 'ask_category':
        session['eligibility_category'] = question.upper()
        stream = session.get('eligibility_stream', 'N/A')
        percentage = session.get('eligibility_percentage', 0)
        category = question.upper()
        
        # Simple Logic for Eligibility
        eligible_msg = ""
        if percentage >= 45:
             eligible_msg = "You seem eligible for most undergraduate courses!"
        else:
             eligible_msg = "You might need to contact the admission office for specific criteria."

        result = f"Based on your details (Stream: {stream}, Percentage: {percentage}%, Category: {category}), {eligible_msg} Please contact our admission cell for final confirmation at 8009902938."
        
        # Clear Session
        session.pop('eligibility_step', None)
        session.pop('eligibility_stream', None)
        session.pop('eligibility_percentage', None)
        session.pop('eligibility_category', None)
        
        return jsonify({
            "answer": result,
            "suggestions": []
        })
    
    return jsonify({
        "answer": "Please start over with 'check eligibility'",
        "suggestions": []
    })

def get_best_match(user_query):
    user_query = preprocess_text(user_query)
    
    # 1. Check Small Talk
    if user_query in small_talk:
        resp = small_talk[user_query]
        return (random.choice(resp) if isinstance(resp, list) else resp), 100, []

    if tfidf_matrix is None:
        return None, 0, []

    # 2. TF-IDF Search
    user_vec = vectorizer.transform([user_query])
    cosine_sims = cosine_similarity(user_vec, tfidf_matrix).flatten()
    
    related_indices = cosine_sims.argsort()[::-1]
    best_idx = related_indices[0]
    best_score = cosine_sims[best_idx] * 100 

    # 3. Fuzzy Fallback
    if best_score < 60:
        fuzzy_best_score = 0
        fuzzy_best_idx = -1
        check_limit = min(len(questions_list), 200) 
        for i in range(check_limit): 
            score = fuzz.token_set_ratio(user_query, questions_list[i])
            if score > fuzzy_best_score:
                fuzzy_best_score = score
                fuzzy_best_idx = i
        
        if fuzzy_best_score > best_score:
            best_score = fuzzy_best_score
            best_idx = fuzzy_best_idx

    matched_faq = answers_map[best_idx]
    
    # Hindi Detection
    hindi_keywords = ['kya', 'hai', 'ka', 'kaise', 'fees', 'kitna', 'kahan', 'kab']
    user_is_speaking_hindi = any(w in user_query for w in hindi_keywords)
    
    if user_is_speaking_hindi and matched_faq.get('answer_hi'):
        answer_text = matched_faq.get('answer_hi')
    else:
        answer_text = matched_faq.get('answer_en') or matched_faq.get('answer')
    
    # Generate Suggestions
    suggestion_pool_indices = related_indices[1:16]
    valid_suggestions = []
    seen_suggestions = set()

    for i in suggestion_pool_indices:
        if cosine_sims[i] > 0.1:
            q_text = questions_list[i]
            if q_text not in seen_suggestions:
                valid_suggestions.append(q_text)
                seen_suggestions.add(q_text)

    final_suggestions = []
    if valid_suggestions:
        count = min(len(valid_suggestions), 3)
        final_suggestions = random.sample(valid_suggestions, count)

    return answer_text, best_score, final_suggestions

# --- ROUTE HANDLERS ---

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/admission_enquiry", methods=["POST"])
def admission_enquiry():
    data = request.get_json()
    try:
        with sqlite3.connect(DATABASE) as conn:
            conn.execute('INSERT INTO enquiries (name, email, phone, course) VALUES (?, ?, ?, ?)',
                         (data.get("name"), data.get("email"), data.get("phone"), data.get("course")))
            conn.commit()
        
        msg = (
            f"🚀 *New Admission Enquiry*\n"
            f"------------------\n"
            f"👤 Name: `{data.get('name')}`\n"
            f"📱 Phone: `{data.get('phone')}`\n"
            f"📧 Email: `{data.get('email')}`\n"
            f"🎓 Course: `{data.get('course')}`"
        )
        send_telegram_alert(msg)

        return jsonify({"success": True, "message": "Enquiry submitted! We will contact you soon."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/book_callback", methods=["POST"])
def book_callback():
    data = request.get_json()
    try:
        with sqlite3.connect(DATABASE) as conn:
            conn.execute('INSERT INTO callbacks (name, phone, preferred_time) VALUES (?, ?, ?)',
                         (data.get("name"), data.get("phone"), data.get("preferred_time")))
            conn.commit()
            
        msg = (
            f"📞 *New Callback Request*\n"
            f"------------------\n"
            f"👤 Name: `{data.get('name')}`\n"
            f"📱 Phone: `{data.get('phone')}`\n"
            f"⏰ Time: `{data.get('preferred_time')}`"
        )
        send_telegram_alert(msg)

        return jsonify({"success": True, "message": "Callback booked successfully!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/admin/report", methods=["GET"])
def trigger_report():
    status = generate_and_send_telegram_report()
    return jsonify({"status": status})
@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json()
    question = data.get("question", "").strip()

    # 1. LOGGING
    try:
        with sqlite3.connect(DATABASE) as conn:
            conn.execute("INSERT INTO queries (user_query) VALUES (?)", (question,))
            conn.commit()
    except Exception as e:
        print(f"Logging error: {e}")

    # 2. CONTINUE FLOW (If already chatting)
    if session.get('eligibility_step'):
        return handle_eligibility_flow(question)
    
    # 3. SMART TRIGGER FOR ELIGIBILITY TOOL
    q_lower = question.lower()
    
    # Define keywords that mean "Run the tool"
    # We include "eligibility" here so even a single word triggers it.
    tool_keywords = [
        'check eligibility', 'am i eligible', 'can i apply', 
        'eligibility checker', 'eligibility check', 'check admission',
        'eligibility', 'eligible', 'eligiblity' # Added common typo
    ]
    
    # A. Check for exact text matches
    direct_match = any(phrase in q_lower for phrase in tool_keywords)
    
    # B. Check for Fuzzy Matches (Handles bad spelling like "eligiblty")
    # We check if the user's input is at least 85% similar to "check eligibility"
    fuzzy_score = fuzz.token_set_ratio("check eligibility", q_lower)
    
    # If the user is specifically asking about a course (e.g., "eligibility for MBA"), 
    # we might want to show the FAQ answer instead of the tool.
    is_specific_course_query = any(c in q_lower for c in ['mba', 'b.tech', 'btech', 'bca', 'bba', 'diploma', 'pharmacy'])

    # LOGIC: 
    # If it matches keywords AND isn't a specific question like "eligibility for MBA" -> Run Tool
    # OR if the fuzzy score is very high (user meant "check eligibility") -> Run Tool
    if (direct_match and not is_specific_course_query) or fuzzy_score > 85:
        session['eligibility_step'] = 'ask_stream'
        return jsonify({
            "answer": "To check your eligibility, please tell me your 12th stream (e.g. PCM, PCB, Commerce, Arts).",
            "suggestions": ["PCM", "Commerce", "PCB"] 
        })

    # 4. STANDARD SEARCH (Database FAQ)
    answer, score, suggestions = get_best_match(question)
    
    # If standard search found nothing good, but the user mentioned "eligibility", default to tool
    if score < 50 and "eligib" in q_lower:
         session['eligibility_step'] = 'ask_stream'
         return jsonify({
            "answer": "I can help check your eligibility. What was your stream in 12th class?",
            "suggestions": ["Science", "Commerce", "Arts"] 
        })

    response_text = ""
    if score > 60:
        response_text = f"{answer}"
    elif score > 40:
        response_text = f"I'm not 100% sure, but here is the closest info I found:\n\n{answer}"
    else:
        response_text = "Sorry, I couldn't find an answer. Please contact admission cell at 8009902938."

    return jsonify({
        "answer": response_text,
        "suggestions": suggestions
    })
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
