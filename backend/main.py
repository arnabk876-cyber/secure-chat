from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from pathlib import Path
from fastapi.staticfiles import StaticFiles
import json

from database import Base, engine, SessionLocal
import models

app = FastAPI(title="Secure Chat")
Base.metadata.create_all(bind=engine)
app.mount("/static",
          StaticFiles(directory=Path(__file__).with_name("static")), 
          name="static")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class RoomCreate(BaseModel):
    name: str


@app.get("/")
def home():
    return {"message": "Secure Chat API is running!"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/rooms")
def create_room(data: RoomCreate, db: Session = Depends(get_db)):
    existing = db.query(models.Room).filter(
        models.Room.name == data.name
    ).first()

    if existing:
        raise HTTPException(status_code=400, detail="Room already exists")

    room = models.Room(name=data.name)
    db.add(room)
    db.commit()
    db.refresh(room)
    return {"id": room.id, "name": room.name}


@app.get("/rooms")
def list_rooms(db: Session = Depends(get_db)):
    rooms = db.query(models.Room).all()
    return [{"id": r.id, "name": r.name} for r in rooms]


@app.get("/chat")
def chat_page():
    return FileResponse(Path(__file__).with_name("chat.html"))


# Connected clients, grouped by room ID
active_rooms: dict[int, list[WebSocket]] = {}


@app.websocket("/ws/{room_id}")
async def chat_socket(websocket: WebSocket, room_id: int):
    db = SessionLocal()

    try:
        room = db.query(models.Room).filter(
            models.Room.id == room_id
        ).first()

        if room is None:
            await websocket.close(code=1008, reason="Room not found")
            return

        await websocket.accept()
        active_rooms.setdefault(room_id, []).append(websocket)

        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)

            sender = str(data.get("sender", "Anonymous"))[:40]
            ciphertext = data.get("ciphertext", "")

            if not isinstance(ciphertext, str) or not ciphertext:
                continue

            # The server stores and relays ciphertext, not plaintext.
            message = models.Message(
                room_id=room_id,
                sender=sender,
                ciphertext=ciphertext
            )
            db.add(message)
            db.commit()

            payload = json.dumps({
                "sender": sender,
                "ciphertext": ciphertext
            })

            for client in list(active_rooms.get(room_id, [])):
                try:
                    await client.send_text(payload)
                except Exception:
                    active_rooms[room_id].remove(client)

    except WebSocketDisconnect:
        pass
    except (json.JSONDecodeError, TypeError):
        await websocket.close(code=1003, reason="Invalid message")
    finally:
        if websocket in active_rooms.get(room_id, []):
            active_rooms[room_id].remove(websocket)
        if not active_rooms.get(room_id):
            active_rooms.pop(room_id, None)
        db.close()