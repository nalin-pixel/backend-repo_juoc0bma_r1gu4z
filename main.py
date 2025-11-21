from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime

# Database utilities are pre-configured in this environment
# - db: Async MongoDB database handle
# - create_document(collection_name, data): inserts with timestamps
# - get_documents(collection_name, filter_dict=None, limit=50): queries documents
try:
    from database import db, create_document, get_documents  # type: ignore
except Exception as e:  # Fallback for local testing if database helpers unavailable
    db = None  # type: ignore
    async def create_document(collection_name: str, data: Dict[str, Any]):  # type: ignore
        return {"_id": "0", **data, "created_at": datetime.utcnow(), "updated_at": datetime.utcnow()}
    async def get_documents(collection_name: str, filter_dict: Optional[Dict[str, Any]] = None, limit: int = 50):  # type: ignore
        return []

app = FastAPI(title="WellMind Assistant API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ====== Schemas ======
class AdviceRequest(BaseModel):
    user_id: str = Field(..., description="Client-generated user/session id")
    message: str = Field(..., min_length=1, max_length=2000)
    mood: Optional[str] = Field(None, description="Optional current mood label")

class AdviceResponse(BaseModel):
    reply: str
    tips: List[str] = []

class ChatMessage(BaseModel):
    user_id: str
    role: str  # "user" | "assistant"
    content: str
    created_at: Optional[datetime] = None

class MoodEntry(BaseModel):
    user_id: str
    mood: str
    note: Optional[str] = None
    created_at: Optional[datetime] = None


# ====== Helpers ======
HEALTH_KEYWORDS = {
    "sleep": ["sleep", "insomnia", "tired", "exhausted", "fatigue"],
    "stress": ["stress", "overwhelmed", "pressure", "anxious", "anxiety", "panic"],
    "mood": ["sad", "down", "depressed", "lonely", "unmotivated"],
    "energy": ["low energy", "drained", "burnout", "exhaustion"],
    "habits": ["exercise", "walk", "water", "hydrate", "nutrition", "diet"],
}

BASIC_TIPS = {
    "sleep": [
        "Aim for a consistent sleep schedule: same bedtime and wake-up every day.",
        "Try a 10-minute wind-down: dim lights, stretch, avoid screens before bed.",
        "Reduce caffeine after midday and keep the bedroom cool and dark.",
    ],
    "stress": [
        "Try box breathing: inhale 4s, hold 4s, exhale 4s, hold 4s (repeat 4x).",
        "Write down the top 3 priorities for today to reduce decision overload.",
        "Take a 5-minute walk to reset your nervous system.",
    ],
    "mood": [
        "Text someone you trust or write a short note to yourself with kindness.",
        "Name what you feel (labeling emotions reduces intensity).",
        "Do a tiny action you enjoy for 2 minutes: music, sun on your face, stretch.",
    ],
    "energy": [
        "Drink a glass of water and have a protein-rich snack.",
        "Stand, roll your shoulders, and do 10 slow breaths.",
        "Step outside for light exposure if possible.",
    ],
    "habits": [
        "Plan a 10-minute activity block you can actually keep.",
        "Fill a water bottle and keep it visible for reminders.",
        "Lay out clothes or gear to make the next action obvious.",
    ],
}


def detect_topics(text: str) -> List[str]:
    text_l = text.lower()
    topics = []
    for key, kws in HEALTH_KEYWORDS.items():
        if any(kw in text_l for kw in kws):
            topics.append(key)
    if not topics:
        topics = ["stress"] if any(w in text_l for w in ["busy", "deadline", "worry"]) else ["mood"]
    return topics


def craft_reply(user_text: str, mood: Optional[str]) -> AdviceResponse:
    topics = detect_topics(user_text)
    lead = "Thanks for sharing. I’m here with you. "
    if mood:
        lead += f"You mentioned feeling {mood.lower()}. "
    lead += "Here’s something gentle and practical you can try right now."

    tips: List[str] = []
    for t in topics[:2]:
        tips.extend(BASIC_TIPS.get(t, []))

    # Pick top 3 tailored tips
    tips = tips[:3] if tips else [
        "Take one small step that feels doable in the next 5 minutes.",
        "Breathe slowly and unclench your jaw and shoulders.",
        "Remember: your feelings are valid, and they will shift.",
    ]

    reply = lead
    return AdviceResponse(reply=reply, tips=tips)


# ====== Routes ======
@app.get("/test")
async def test():
    try:
        if db is not None:
            await get_documents("ping", {}, 1)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/advice", response_model=AdviceResponse)
async def get_advice(payload: AdviceRequest):
    # Generate rule-based supportive response
    advice = craft_reply(payload.message, payload.mood)

    # Persist chat messages
    user_msg = {
        "user_id": payload.user_id,
        "role": "user",
        "content": payload.message,
    }
    bot_msg = {
        "user_id": payload.user_id,
        "role": "assistant",
        "content": advice.reply + "\n\n" + "\n".join(f"• {t}" for t in advice.tips),
    }
    try:
        await create_document("message", user_msg)
        await create_document("message", bot_msg)
    except Exception:
        pass

    return advice


class MessagesQuery(BaseModel):
    user_id: str
    limit: int = 50

@app.post("/messages", response_model=List[ChatMessage])
async def list_messages(query: MessagesQuery):
    try:
        docs = await get_documents(
            "message",
            {"user_id": query.user_id},
            min(query.limit, 100),
        )
        # Sort by created_at if present
        docs_sorted = sorted(
            docs,
            key=lambda d: d.get("created_at", datetime.utcnow()),
        )
        return [
            ChatMessage(
                user_id=d.get("user_id", ""),
                role=d.get("role", "assistant"),
                content=d.get("content", ""),
                created_at=d.get("created_at"),
            )
            for d in docs_sorted
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class MoodCreate(BaseModel):
    user_id: str
    mood: str
    note: Optional[str] = None

@app.post("/mood", response_model=MoodEntry)
async def create_mood(entry: MoodCreate):
    doc = entry.dict()
    try:
        saved = await create_document("mood", doc)
    except Exception:
        saved = {**doc, "created_at": datetime.utcnow()}
    return MoodEntry(
        user_id=saved.get("user_id", entry.user_id),
        mood=saved.get("mood", entry.mood),
        note=saved.get("note"),
        created_at=saved.get("created_at"),
    )


class MoodQuery(BaseModel):
    user_id: str
    limit: int = 30

@app.post("/mood/list", response_model=List[MoodEntry])
async def list_moods(query: MoodQuery):
    try:
        docs = await get_documents(
            "mood",
            {"user_id": query.user_id},
            min(query.limit, 100),
        )
        docs_sorted = sorted(
            docs,
            key=lambda d: d.get("created_at", datetime.utcnow()),
        )
        return [
            MoodEntry(
                user_id=d.get("user_id", ""),
                mood=d.get("mood", ""),
                note=d.get("note"),
                created_at=d.get("created_at"),
            )
            for d in docs_sorted
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
