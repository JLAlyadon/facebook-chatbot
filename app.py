import requests
from flask import Flask, request, jsonify
import mysql.connector
from difflib import SequenceMatcher

app = Flask(__name__)

# MySQL database connection
db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="APSB2025",
    database="chatbot_db"
)

VERIFY_TOKEN = "APSB2025_token"
PAGE_ACCESS_TOKEN = "EABZC4X9LZB8fEBOwbGu29DnmNCZBQrF45ZA2ytwLqmGq3nSZC3yChl6EWKEFeb74NqPk6zSQqeeKe8ergw3nbrnTyg7SRCeCUNIAEXosKkUA75SurIJTGJo7KIv6LYjIyLxTf1faRjR0KczkiauOtSzv6R76lCdjaaZBAF2TcpsZCV4HsBgVW6qwZBph6fXqNoyO5gZDZD"

live_agent_message_sent = {}

def send_message(recipient_id, text):
    """Send a message to the user via Facebook Messenger API."""
    url = f"https://graph.facebook.com/v16.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
    }
    requests.post(url, json=payload, headers=headers)

def get_user_first_name(user_id):
    """Fetch the user's first name using Facebook Graph API."""
    url = f"https://graph.facebook.com/{user_id}?fields=first_name&access_token={PAGE_ACCESS_TOKEN}"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json().get("first_name", "there")
    return "there"

def fuzzy_match(input_text, stored_text):
    """Calculate similarity between two texts using fuzzy matching."""
    return SequenceMatcher(None, input_text.lower(), stored_text.lower()).ratio()

def get_best_faq_match(user_message):
    """Find the closest matching FAQ question using fuzzy matching."""
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT id, question, answer FROM faqs")
    faqs = cursor.fetchall()
    cursor.close()

    best_match, best_score = None, 0

    for faq in faqs:
        score = fuzzy_match(user_message, faq["question"])
        if score > best_score:
            best_score, best_match = score, faq

    return best_match["answer"] if best_match and best_score >= 0.7 else None

def get_keyword_or_synonym_match(user_message):
    """Find an answer using stored keywords and synonyms, handling multiple keyword matches."""
    cursor = db.cursor(dictionary=True)

    # Check for keyword matches (allow multiple results)
    cursor.execute("""
        SELECT faqs.answer FROM keywords 
        JOIN faqs ON keywords.faq_id = faqs.id 
        WHERE %s LIKE CONCAT('%', keywords.keyword, '%')
    """, (user_message,))
    
    keyword_matches = cursor.fetchall()  # Fetch all matching answers

    # Check for synonym matches (allow multiple results)
    cursor.execute("""
        SELECT faqs.answer FROM synonyms 
        JOIN keywords ON synonyms.keyword_id = keywords.id
        JOIN faqs ON keywords.faq_id = faqs.id
        WHERE %s LIKE CONCAT('%', synonyms.synonym, '%')
    """, (user_message,))
    
    synonym_matches = cursor.fetchall()  # Fetch all matching answers
    cursor.close()

    # Combine both keyword and synonym matches
    all_matches = keyword_matches + synonym_matches

    if all_matches:
        # Return the most frequent answer if multiple matches exist
        answers = [match["answer"] for match in all_matches]
        return max(set(answers), key=answers.count)  # Return most common answer

    return None  # No match found


def find_faq_answer(user_message):
    """Find the best response using fuzzy matching, keywords, and synonyms."""
    return get_best_faq_match(user_message) or get_keyword_or_synonym_match(user_message)

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':  
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        
        if token == VERIFY_TOKEN:
            return str(challenge)  # Ensure it returns a string, not an int
        else:
            return "Invalid verify token", 403


    elif request.method == 'POST':
        data = request.get_json()
        user_id = data['entry'][0]['messaging'][0]['sender']['id']
        message_text = data['entry'][0]['messaging'][0].get('message', {}).get('text', "").lower()

        cursor = db.cursor(dictionary=True)

        try:
            # Check if user exists
            cursor.execute("SELECT is_bot_active FROM users WHERE user_id = %s", (user_id,))
            user = cursor.fetchone()

            if not user:
                # First-time user: Save to DB and send an intro message
                first_name = get_user_first_name(user_id)
                cursor.execute("INSERT INTO users (user_id, is_bot_active) VALUES (%s, %s)", (user_id, True))
                db.commit()
                send_message(
                    user_id,
                    f"Hi, {first_name}.\nI'm an AKO PARA SA BATA chatbot, and I'm here to help. What do you need? "
                    "If you want to chat with a live agent, just type 'live agent,' or stay with me for quick assistance."
                )
                return jsonify({'status': 'success'}), 200

            # Toggle live agent mode
            if message_text in ["live agent", "talk to agent"]:
                cursor.execute("UPDATE users SET is_bot_active = %s WHERE user_id = %s", (False, user_id))
                db.commit()
                send_message(user_id, "You're now chatting with a live agent.")
                return jsonify({'status': 'success'}), 200

            elif message_text == "chatbot":
                cursor.execute("UPDATE users SET is_bot_active = %s WHERE user_id = %s", (True, user_id))
                db.commit()
                send_message(user_id, "You're back with the chatbot. How can I assist you?")
                live_agent_message_sent[user_id] = False  
                return jsonify({'status': 'success'}), 200

            # If user is in live agent mode, don't process as a bot
            if not user["is_bot_active"]:
                if user_id not in live_agent_message_sent or not live_agent_message_sent[user_id]:
                    send_message(user_id, "You're chatting with a live agent. Please wait. Type 'chatbot' to return to the bot.")
                    live_agent_message_sent[user_id] = True
                return jsonify({'status': 'live agent mode'}), 200

            # Try to find a response based on fuzzy match, keywords, or synonyms
            response = find_faq_answer(message_text)

            if response:
                send_message(user_id, response)
            else:
                # If no match, switch to live agent
                cursor.execute("UPDATE users SET is_bot_active = %s WHERE user_id = %s", (False, user_id))
                db.commit()
                send_message(user_id, "I couldn't understand your message, so I've connected you to a live agent. They'll respond shortly.")
                return jsonify({'status': 'switched to live agent'}), 200

            # Store message in DB
            cursor.execute(
                "INSERT INTO messages (user_id, message, response) VALUES (%s, %s, %s)",
                (user_id, message_text, response if response else "Live agent triggered")
            )
            db.commit()

        finally:
            cursor.close()

        return jsonify({'status': 'success'}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
