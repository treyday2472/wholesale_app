# app/voicebot.py (optional file to keep routes tidy)
from flask import Blueprint, request, current_app, jsonify

import time

voice = Blueprint('voice', __name__)

# naive in-memory store; move to Redis in prod
CALL_STATE = {}  # call_uuid -> {"idx": int, "answers": {}, "tries": {"occupancy_status":0,"listed_with_realtor":0,"condition":0}}

QUESTIONS = [
  {"key":"why_sell", "prompt":"Why do you want to sell?"},
  {"key":"occupancy_status", "prompt":"Is it owner occupied, rented out, or vacant?", "required": True},
  {"key":"listed_with_realtor", "prompt":"Is the property listed with a realtor?", "required": True},
  {"key":"condition", "prompt":"On a scale of 1 to 10, what would you say the condition is?", "required": True},
  {"key":"repairs_needed", "prompt":"What repairs are needed? You can say cosmetics, light repairs, heavy repairs, or complete rehab."},
  {"key":"repairs_cost_est", "prompt":"How much do you think the repairs will run and why?"},
  {"key":"worth_estimate", "prompt":"What do you think the property is worth?"},
  {"key":"behind_on_payments", "prompt":"Are you behind on payments?"},
  # … keep adding your keys in order …
]

def ncco_ask(text, event_url):
    return [
      {"action": "talk", "text": text},
      {"action": "input", "type": ["speech"], "speech": {"endOnSilence": 1}, "eventUrl": [event_url]}
    ]

@voice.route("/voice/answer", methods=["GET"])
def answer():
    call_uuid = request.args.get("uuid")
    CALL_STATE[call_uuid] = {"idx": 0, "answers": {}, "tries":{"occupancy_status":0,"listed_with_realtor":0,"condition":0}}
    q = QUESTIONS[0]
    return jsonify(ncco_ask(q["prompt"], f"{request.url_root.rstrip('/')}/voice/input"))

@voice.route("/voice/input", methods=["POST"])
def input_event():
    payload = request.get_json() or {}
    call_uuid = payload.get("uuid")
    st = CALL_STATE.get(call_uuid)
    if not st:  # no state? restart
        return jsonify(ncco_ask(QUESTIONS[0]["prompt"], f"{request.url_root.rstrip('/')}/voice/input"))

    # parse recognized speech
    text = ""
    if payload.get("speech") and payload["speech"].get("results"):
        text = (payload["speech"]["results"][0].get("text") or "").strip().lower()

    # current question
    q = QUESTIONS[st["idx"]]
    key = q["key"]

    # normalize a few keys (you can extend this)
    if key == "occupancy_status":
        if "owner" in text: text = "owner_occupied"
        elif "rent" in text: text = "rented"
        elif "vacant" in text: text = "vacant"
        else: text = ""

    if key == "listed_with_realtor":
        if text in ["yes","yeah","yep","yup","affirmative","true"]: text = "yes"
        elif text in ["no","nope","nah","false"]: text = "no"

    if key == "condition":
        # try to extract a number 1-10
        import re
        m = re.search(r"\b(10|[1-9])\b", text)
        text = m.group(1) if m else ""

    # required logic with 3-attempt rule
    if q.get("required") and not text:
        st["tries"][key] += 1
        if key == "condition" and st["tries"][key] >= 3:
            text = "7"  # default after 3 tries
        elif st["tries"][key] >= 3:
            # allow blank for other required after 3 tries
            pass
        else:
            # reprompt same question
            return jsonify(ncco_ask("Sorry, I didn't catch that. " + q["prompt"], f"{request.url_root.rstrip('/')}/voice/input"))

    # store answer (may be "")
    st["answers"][key] = text

    # advance
    st["idx"] += 1
    if st["idx"] >= len(QUESTIONS):
        # finalize: post to your universal endpoint (or call db directly)
        # Here we just say thanks and hang up.
        return jsonify([
            {"action":"talk", "text":"Thanks. We’ve captured your information. Someone will follow up shortly. Goodbye."}
        ])

    next_q = QUESTIONS[st["idx"]]
    return jsonify(ncco_ask(next_q["prompt"], f"{request.url_root.rstrip('/')}/voice/input"))
