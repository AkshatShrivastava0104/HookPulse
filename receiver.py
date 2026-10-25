from fastapi import FastAPI, Request

app = FastAPI()

@app.post("/receiver")
async def receive_webhook(request: Request):
    payload = await request.json()
    print("Received webhook:", payload.get("message_id"))
    return {"received": True}